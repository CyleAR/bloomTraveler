"use strict";
const $ = (id) => document.getElementById(id);
const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const glyph = (name) => `<i data-lucide="${name}"></i>`;
const icons = () => lucide.createIcons({attrs:{"aria-hidden":"true"}});
const clone = (value) => JSON.parse(JSON.stringify(value));
const coords = (point) => `${point.lat.toFixed(6)}, ${point.lon.toFixed(6)}`;
const lnglat = (point) => [point.lon, point.lat];
const metres = (value) => value >= 1000 ? `${(value / 1000).toFixed(2)} km` : `${Math.round(value)} m`;
let token, state, route, revision = 0, library = {routes:[],places:[]}, savedId = null;
let dirty = false, selected = null, selectedIndex = -1, mode = "select", follow = true;
let realMarker, simMarker, selectedMarker, mapReady = false, initialCentered = false, wantReal = false;
let lastRealRevision = -1, lastPosition = "", lastLog = 0, toastTimer;
let undoStack = [], redoStack = [], routeJobs = Promise.resolve(), currentDialog = null;
let keys = new Set(), keyTimer, activeTab = "route", dragIndex = -1;
let pointDrag = null, pointDragFrame = null, ignoreMapClickUntil = 0;
const rowHeight = 74;
const emptyRoute = () => ({name:"새 경로",points:[],repeat:false,return_mode:"instant",return_speed:20,repeat_count:0,template:null});
const mapStyles = new Set(["osm", "bloom"]);
let activeMapStyle = "osm";
function mapStyle(value) {
  if (value === "bloom") return "/styles/bloom.json";
  return {
    version:8, name:"OpenStreetMap 기본",
    glyphs:"https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    sources:{"osm-standard":{
      type:"raster", tiles:["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize:256,
      minzoom:0, maxzoom:19,
      attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>'
    }},
    layers:[{id:"osm-standard",type:"raster",source:"osm-standard",paint:{"raster-fade-duration":150}}]
  };
}
async function changeMapStyle(value, persist=true) {
  cancelPointDrag();
  activeMapStyle = mapStyles.has(value) ? value : "osm";
  $("map-style").value = activeMapStyle;
  mapReady=false;
  // A full style reload also reinstalls the route overlays for raster/vector switches.
  map.setStyle(mapStyle(activeMapStyle),{diff:false});
  if(persist) library.preferences=await api("preferences",{map_style:activeMapStyle});
}
function parseCoordinates(text) {
  const value=text.trim().replaceAll('，',',');
  const parts=value.includes(',')?value.split(',').map(part=>part.trim()):value.split(/\s+/);
  const decimal=/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
  if(parts.length!==2 || parts.some(part=>!decimal.test(part))) throw new Error('좌표를 위도, 경도 순서로 입력하세요');
  const [lat,lon]=parts.map(Number);
  if(!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat)>90 || Math.abs(lon)>180) throw new Error('위도는 -90~90, 경도는 -180~180 범위여야 합니다');
  return {lat,lon,name:''};
}

async function api(path, data) {
  const response = await fetch(`/api/${path}`, data === undefined ? {cache:"no-store"} : {
    method:"POST",headers:{"Content-Type":"application/json","X-Bloom-Token":token},body:JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "요청 실패");
  return result;
}
function toast(message, error = false) {
  $("toast").textContent = message;
  $("toast").classList.toggle("error", error);
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").hidden = true, error ? 6000 : 3000);
}
const safely = (fn) => (...args) => Promise.resolve().then(() => fn(...args)).catch((error) => toast(error.message, true));
async function action(type, extra = {}) {
  state = await api("action", {type,...extra});
  renderState();
  return state;
}
function enqueueRoute(job) {
  const task = routeJobs.then(job);
  routeJobs = task.catch(() => {});
  return task;
}
function keepHistory(old) {
  undoStack.push(old);
  while (undoStack.length > 20 || (undoStack.length > 1 && undoStack.reduce((size, x) => size + JSON.stringify(x).length, 0) > 4000000)) undoStack.shift();
}
async function acceptRoute(result, markDirty = true) {
  cancelPointDrag();
  route = result.route;
  revision = result.revision;
  if(result.state)state=result.state;
  state.length_m = result.length_m;
  state.point_count = route.points.length;
  selectedIndex = Math.min(selectedIndex, route.points.length - 1);
  dirty = markDirty;
  renderRoute();
  renderState();
}
function modifyRoute(edit) {
  return enqueueRoute(async () => {
    const old = clone(route), next = clone(route);
    edit(next);
    const result = await api("route", {route:next,revision});
    keepHistory(old);
    redoStack = [];
    await acceptRoute(result);
  });
}
async function historyStep(undo) {
  return enqueueRoute(async () => {
    const source = undo ? undoStack : redoStack;
    const destination = undo ? redoStack : undoStack;
    if (!source.length) return;
    const result = await api("route", {route:source[source.length-1],revision});
    source.pop(); destination.push(clone(route));
    await acceptRoute(result);
  });
}
function showDialog(title, fields, confirm = "저장") {
  if (currentDialog) currentDialog(null);
  $("dialog-title").textContent = title;
  $("dialog-fields").innerHTML = fields;
  $("dialog-error").hidden = true;
  $("confirm-dialog").textContent = confirm;
  icons();
  if (!$("editor-dialog").open) $("editor-dialog").showModal();
  const input = $("dialog-fields").querySelector("input:not([type=checkbox])");
  if (input) { input.focus(); input.select(); }
  return new Promise((resolve) => { currentDialog = resolve; });
}
function closeDialog(value = null) {
  const resolve = currentDialog;
  currentDialog = null;
  $("editor-dialog").close();
  if (resolve) resolve(value);
}
async function confirmAction(title, text, label = "확인") {
  return Boolean(await showDialog(title, `<p class="confirm-copy">${esc(text)}</p>`, label));
}
async function nameDialog(title, name, copy = false) {
  return showDialog(title, `<label class="form-field">이름<input name="name" required maxlength="200" value="${esc(name)}"></label>${copy ? '<label class="form-check"><input type="checkbox" name="copy">새 경로로 저장</label>' : ''}`);
}
async function confirmReplace() {
  return !dirty || confirmAction("현재 경로 바꾸기", "저장하지 않은 경로 변경을 버릴까요?", "바꾸기");
}
function setTab(tab) {
  activeTab = tab;
  document.querySelectorAll(".tab").forEach((button) => {
    const active = button.dataset.tab === tab;
    button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".side-view").forEach((view) => view.classList.toggle("active", view.id === `${tab}-view`));
  if (tab === "route") renderList(); else renderLibrary();
}
function setFollow(value) {
  follow = value;
  $("follow-map").classList.toggle("active", value);
  $("follow-map").setAttribute("aria-pressed", String(value));
}
function centre(point, zoom = 16, keepFollow = false) {
  wantReal = false;
  initialCentered = true;
  if(!keepFollow)setFollow(false);
  if (map) map.flyTo({center:lnglat(point),zoom,essential:true,duration:700});
}
function selectPlace(point, index = -1, fly = false) {
  selected = clone(point); selectedIndex = index;
  $("selected-place").hidden = false;
  $("selected-name").textContent = point.name || (index >= 0 ? `지점 ${index+1}` : "선택한 위치");
  $("selected-address").textContent = point.address || "";
  $("selected-address").hidden = !point.address;
  $("selected-coords").textContent = coords(point);
  if (!selectedMarker) {
    const element = document.createElement("div"); element.className = "selection-marker";
    selectedMarker = new maplibregl.Marker({element}).setLngLat(lnglat(point)).addTo(map);
  } else selectedMarker.setLngLat(lnglat(point));
  if (fly) centre(point);
  renderList();
}
function clearSelection() {
  selected = null; selectedIndex = -1;
  $("selected-place").hidden = true;
  if (selectedMarker) {selectedMarker.remove();selectedMarker=null;}
  renderList();
}
function addPoint(point) {
  return modifyRoute((value) => {
    value.points.push({lat:point.lat,lon:point.lon,name:point.name || "",speed:null,wait:0,instant:false,
                       segment:value.points.at(-1)?.segment ?? 0});
    selectedIndex = value.points.length - 1;
  }).then(() => {setTab("route");$("route-list").scrollTop = Math.max(0, route.points.length * rowHeight - $("route-list").clientHeight);renderList();});
}
async function editPoint(index = -1) {
  const point = index >= 0 ? route.points[index] : {lat:selected?.lat ?? state.real_location?.lat ?? 0,lon:selected?.lon ?? state.real_location?.lon ?? 0,name:"",speed:null,wait:0,instant:false};
  const fields = `<label class="form-field">지점 이름<input name="name" maxlength="200" value="${esc(point.name)}"></label><div class="form-grid"><label class="form-field">위도<input name="lat" type="number" step="any" min="-90" max="90" required value="${point.lat}"></label><label class="form-field">경도<input name="lon" type="number" step="any" min="-180" max="180" required value="${point.lon}"></label></div><div class="form-grid"><label class="form-field">지점 속도 (km/h)<input name="speed" type="number" min="0.1" max="1000" step="0.1" placeholder="기본 속도" value="${point.speed ?? ''}"></label><label class="form-field">도착 후 대기 (초)<input name="wait" type="number" min="0" max="86400" step="0.1" required value="${point.wait ?? 0}"></label></div><label class="form-check"><input name="instant" type="checkbox" ${point.instant ? 'checked' : ''}>이 지점으로 순간이동</label>${index >= 0 ? `<div class="point-tools"><button type="button" id="point-up" class="button secondary" ${index === 0 ? 'disabled' : ''}>${glyph('arrow-up')}위로</button><button type="button" id="point-down" class="button secondary" ${index === route.points.length-1 ? 'disabled' : ''}>${glyph('arrow-down')}아래로</button><button type="button" id="point-delete" class="button secondary">${glyph('trash-2')}삭제</button></div>` : ''}`;
  const pending = showDialog(index >= 0 ? `지점 ${index+1} 편집` : "좌표로 지점 추가", fields, index >= 0 ? "적용" : "추가");
  if (index >= 0) {
    $("point-up").onclick = () => closeDialog({move:-1});
    $("point-down").onclick = () => closeDialog({move:1});
    $("point-delete").onclick = () => closeDialog({remove:true});
  }
  const result = await pending;
  if (!result) return;
  if (result.remove) return removePoint(index);
  if (result.move) return movePoint(index,result.move);
  await modifyRoute((value) => {
      const updates = {name:result.name,lat:Number(result.lat),lon:Number(result.lon),speed:result.speed ? Number(result.speed) : null,wait:Number(result.wait),instant:result.instant === "on"};
      if (index < 0) value.points.push({...updates,segment:value.points.at(-1)?.segment ?? 0});
      else Object.assign(value.points[index],updates);
  });
  if (selectedIndex >= 0 && route.points[selectedIndex]) selectPlace(route.points[selectedIndex],selectedIndex);
}
async function movePoint(index,direction) {
  const target=index+direction;
  if(target<0 || target>=route.points.length)return;
  await modifyRoute(value=>{
    [value.points[index],value.points[target]]=[value.points[target],value.points[index]];
    if(selectedIndex===index)selectedIndex=target;
    else if(selectedIndex===target)selectedIndex=index;
  });
  if(selectedIndex>=0)selectPlace(route.points[selectedIndex],selectedIndex);
}
async function removePoint(index) {
  await modifyRoute(value=>{
    value.points.splice(index,1);
    if(selectedIndex===index)clearSelection();
    else if(selectedIndex>index)selectedIndex--;
  });
  if(selectedIndex>=0)selectPlace(route.points[selectedIndex],selectedIndex);
}
function renderList() {
  if (!route) return;
  const inner = $("route-list-inner"), count = route.points.length;
  if (!count) {inner.style.height="";inner.innerHTML=`<div class="empty">${glyph('route')}<span>아직 경로가 없습니다</span></div>`;icons();return;}
  inner.style.height = `${count*rowHeight}px`;
  const start = Math.max(0,Math.floor($("route-list").scrollTop / rowHeight)-3);
  const end = Math.min(count,start+Math.ceil($("route-list").clientHeight/rowHeight)+7);
  inner.innerHTML = route.points.slice(start,end).map((p, offset) => {
    const index = start+offset;
    return `<div class="waypoint-row ${selectedIndex === index ? 'selected' : ''}" data-index="${index}" style="top:${index*rowHeight}px" draggable="true" role="button" tabindex="0" aria-label="${esc(p.name || `지점 ${index+1}`)}"><span class="point-number">${index+1}</span><div class="point-detail"><strong>${esc(p.name || (index === 0 ? '출발점' : index === count-1 ? '도착점' : `지점 ${index+1}`))}</strong><small>${coords(p)}</small><div class="point-meta">${p.instant ? `${glyph('zap')}순간이동` : `${glyph('gauge')}${p.speed ?? state.speed} km/h`}${p.wait ? `${glyph('timer')}${p.wait}초 대기` : ''}${index>0 && p.segment!==route.points[index-1].segment ? ' · 새 구간' : ''}</div></div><div class="waypoint-actions"><button class="icon-button row-up" title="위로" aria-label="지점 ${index+1} 위로" ${index===0?'disabled':''}>${glyph('arrow-up')}</button><button class="icon-button row-down" title="아래로" aria-label="지점 ${index+1} 아래로" ${index===count-1?'disabled':''}>${glyph('arrow-down')}</button><button class="icon-button row-edit" title="지점 편집" aria-label="지점 ${index+1} 편집">${glyph('sliders-horizontal')}</button><button class="icon-button danger row-delete" title="삭제" aria-label="지점 ${index+1} 삭제">${glyph('trash-2')}</button></div></div>`;
  }).join("");
  inner.querySelectorAll(".waypoint-row").forEach((row) => {
    const index = Number(row.dataset.index);
    row.onclick = (event) => {if(!event.target.closest("button"))selectPlace(route.points[index],index,true);};
    row.onkeydown = (event) => {if(event.key==="Enter" && !event.target.closest('button'))selectPlace(route.points[index],index,true);};
    row.ondblclick = safely(event => {if(!event.target.closest('button'))return editPoint(index);});
    row.querySelector(".row-edit").onclick = safely(() => editPoint(index));
    row.querySelector(".row-up").onclick = safely(() => movePoint(index,-1));
    row.querySelector(".row-down").onclick = safely(() => movePoint(index,1));
    row.querySelector(".row-delete").onclick = safely(() => removePoint(index));
    row.ondragstart = (event) => {dragIndex=index;event.dataTransfer.effectAllowed="move";event.dataTransfer.setData("text/plain",String(index));};
    row.ondragover = (event) => {if(dragIndex>=0){event.preventDefault();row.classList.add("drag-over");}};
    row.ondragleave = () => row.classList.remove("drag-over");
    row.ondrop = safely(async (event) => {event.preventDefault();row.classList.remove("drag-over");const from=dragIndex;dragIndex=-1;if(from<0 || from===index)return;await modifyRoute(value=>{const [point]=value.points.splice(from,1);value.points.splice(index,0,point);selectedIndex=index;});});
    row.ondragend = () => {dragIndex=-1;document.querySelectorAll(".drag-over").forEach(x=>x.classList.remove("drag-over"));};
  });
  icons();
}
function approximateDistance(a,b) {
  const rad=Math.PI/180,dlat=(b.lat-a.lat)*rad,dlon=(b.lon-a.lon)*rad;
  const h=Math.sin(dlat/2)**2+Math.cos(a.lat*rad)*Math.cos(b.lat*rad)*Math.sin(dlon/2)**2;
  return 12742000*Math.asin(Math.min(1,Math.sqrt(h)));
}
function renderStats() {
  $("route-name").textContent = route.name;
  $("route-name").title = route.name;
  $("route-name").classList.toggle("dirty",dirty);
  $("route-length").textContent = metres(state.length_m);
  $("point-count").textContent = route.points.length.toLocaleString();
  let seconds=0;
  route.points.forEach((p,index)=>{seconds+=p.wait || 0;if(index && !p.instant && p.segment===route.points[index-1].segment)seconds+=approximateDistance(route.points[index-1],p)/(p.speed || state.speed)*3.6;});
  $("route-time").textContent = seconds < 3600 ? `${Math.ceil(seconds/60)}분` : `${Math.floor(seconds/3600)}시간 ${Math.ceil(seconds%3600/60)}분`;
  $("route-time").title="편도 경로 기준";
  for(const id of ['route-length','point-count','route-time']){
    const element=$(id);element.style.fontSize='';
    let size=parseFloat(getComputedStyle(element).fontSize);
    while(element.scrollWidth>element.clientWidth && size>11){element.style.fontSize=`${--size}px`;}
  }
}
function renderRoute() {
  renderStats(); renderList(); updateMapRoute();
  $("save-route").disabled = $("export-gpx").disabled = !route.points.length;
  $("goto-start").disabled = !route.points.length;
  $("reverse-route").disabled = route.points.length < 2;
  $("clear-route").disabled = !route.points.length;
  $("undo-route").disabled = !undoStack.length;
  $("redo-route").disabled = !redoStack.length;
  $("repeat-route").checked = route.repeat;
  $("repeat-options").hidden = !route.repeat;
  document.querySelectorAll("[data-return]").forEach(button=>button.classList.toggle("active",button.dataset.return===route.return_mode));
  const counts=[0,2,3,5,10];
  $("repeat-count").value = counts.includes(route.repeat_count) ? String(route.repeat_count) : "custom";
  $("custom-count-field").hidden = counts.includes(route.repeat_count);
  if(!counts.includes(route.repeat_count))$("custom-count").value=route.repeat_count;
  $("return-speed").value=route.return_speed;
  $("return-speed-field").hidden=route.return_mode==="instant";
}
async function refreshLibrary() {library=await api("library");renderLibrary();}
function renderLibrary() {
  $("saved-route-count").textContent=library.routes.length;
  $("saved-place-count").textContent=library.places.length;
  for(const kind of ["routes","places"]) {
    const filter=$(kind==="routes"?"route-filter":"place-filter").value.toLocaleLowerCase();
    const items=library[kind].filter(item=>item.name.toLocaleLowerCase().includes(filter));
    const host=$(kind==="routes"?"saved-routes":"saved-places");
    host.innerHTML=items.length ? items.map(item=>`<div class="library-row" data-id="${esc(item.id)}"><button class="library-open" title="${kind==='routes'?'경로 불러오기':'지도에서 보기'}">${glyph(kind==='routes'?'route':'star')}<div><strong>${esc(item.name)}</strong><small>${kind==='routes'?`${item.points.length.toLocaleString()}개 지점` : coords(item)}</small></div></button><button class="icon-button rename-item" title="이름 변경" aria-label="${esc(item.name)} 이름 변경">${glyph('pencil')}</button><button class="icon-button danger delete-item" title="삭제" aria-label="${esc(item.name)} 삭제">${glyph('trash-2')}</button></div>`).join("") : `<div class="empty">${glyph(kind==='routes'?'folder-open':'star')}<span>${filter?'검색 결과가 없습니다':kind==='routes'?'저장한 경로가 없습니다':'즐겨찾는 장소가 없습니다'}</span></div>`;
    host.querySelectorAll(".library-row").forEach(row=>{
      const item=items.find(x=>x.id===row.dataset.id);
      row.querySelector(".library-open").onclick=safely(async()=>{
        if(kind==="places"){selectPlace(item,-1,true);return;}
        if(!await confirmReplace())return;
        await enqueueRoute(async()=>{const result=await api("route",{route:item,revision});undoStack=[];redoStack=[];savedId=item.id;clearSelection();await acceptRoute(result,false);});
        setTab("route");fitRoute();toast("경로를 불러왔습니다");
      });
      row.querySelector(".rename-item").onclick=safely(async()=>{const result=await nameDialog("이름 변경",item.name);if(!result)return;await api("library/save",{kind,id:item.id,value:{...item,name:result.name}});await refreshLibrary();if(kind==='routes' && savedId===item.id)await modifyRoute(value=>value.name=result.name);});
      row.querySelector(".delete-item").onclick=safely(async()=>{if(!await confirmAction("저장 항목 삭제",`“${item.name}”을 삭제할까요?`,"삭제"))return;await api("library/delete",{kind,id:item.id});if(savedId===item.id)savedId=null;await refreshLibrary();});
    });
  }
  icons();
}
async function saveRoute() {
  if(!route.points.length)return;
  const result=await nameDialog("경로 저장",route.name,Boolean(savedId));
  if(!result)return;
  await modifyRoute(value=>value.name=result.name.trim());
  const saved=await api("library/save",{kind:"routes",id:result.copy ? null : savedId,value:route});
  savedId=saved.id;dirty=false;renderStats();await refreshLibrary();toast("경로를 저장했습니다");
}
async function favoritePlace() {
  if(!selected)return;
  const point=clone(selected),result=await nameDialog("장소 즐겨찾기",point.name || "즐겨찾는 장소");
  if(!result)return;
  await api("library/save",{kind:"places",value:{...point,name:result.name}});
  await refreshLibrary();toast("장소를 저장했습니다");
}
function fitRoute() {
  if(!map || !route.points.length)return;
  wantReal=false;
  if(route.points.length===1){centre(route.points[0]);return;}
  const bounds=new maplibregl.LngLatBounds();
  route.points.forEach(point=>bounds.extend(lnglat(point)));
  initialCentered=true;setFollow(false);map.fitBounds(bounds,{padding:{top:90,bottom:95,left:40,right:65},maxZoom:17,duration:700});
}
function updateMapRoute() {
  if(!mapReady || !map.getSource("bloom-route"))return;
  const preview = pointDrag?.moved ? pointDrag : null;
  const routePoints = preview ? route.points.map(point=>point.id===preview.id?{...point,...preview.position}:point) : route.points;
  const groups=[];
  routePoints.forEach((point,index)=>{if(!index || point.segment!==routePoints[index-1].segment)groups.push([]);groups.at(-1).push(lnglat(point));});
  map.getSource("bloom-route").setData({type:"FeatureCollection",features:groups.filter(x=>x.length>1).map(x=>({type:"Feature",properties:{},geometry:{type:"LineString",coordinates:x}}))});
  const points=routePoints.map((point,index)=>({type:"Feature",properties:{index,label:routePoints.length<=300 || index===0 || index===routePoints.length-1 ? String(index+1):""},geometry:{type:"Point",coordinates:lnglat(point)}}));
  map.getSource("bloom-points").setData({type:"FeatureCollection",features:points});
  const returning=route.repeat && route.return_mode==='straight' && route.points.length>1;
  map.getSource("bloom-return").setData({type:"FeatureCollection",features:returning?[{type:"Feature",properties:{},geometry:{type:"LineString",coordinates:[lnglat(routePoints.at(-1)),lnglat(routePoints[0])]}}]:[]});
}
function releasePointDrag() {
  const drag=pointDrag;
  if(!drag)return null;
  pointDrag=null;
  cancelAnimationFrame(pointDragFrame);pointDragFrame=null;
  const canvas=map.getCanvas();
  if(canvas.hasPointerCapture(drag.pointer))canvas.releasePointerCapture(drag.pointer);
  if(drag.panEnabled)map.dragPan.enable();
  if(drag.zoomEnabled){map.touchZoomRotate.enable();map.touchZoomRotate.disableRotation();}
  canvas.style.cursor=mode==='route'?'crosshair':'';
  ignoreMapClickUntil=performance.now()+250;
  return drag;
}
function cancelPointDrag() {
  const drag=releasePointDrag();
  if(!drag)return;
  updateMapRoute();
  const index=route.points.findIndex(point=>point.id===drag.id);
  if(index>=0)selectPlace(route.points[index],index);
  else clearSelection();
}
function wirePointDragging() {
  const canvas=map.getCanvas();
  const screenPoint=event=>{const rect=canvas.getBoundingClientRect();return [event.clientX-rect.left,event.clientY-rect.top];};
  const hitPoint=position=>map.getLayer('bloom-point-circles')?map.queryRenderedFeatures(position,{layers:['bloom-point-circles']})[0]:null;
  map.getCanvasContainer().addEventListener('pointerdown',event=>{
    if(pointDrag || event.button!==0 || !event.isPrimary || !mapReady)return;
    const screen=screenPoint(event),feature=hitPoint(screen);
    if(!feature)return;
    const index=Number(feature.properties.index),point=route.points[index];
    if(!point)return;
    event.preventDefault();
    map.stop();
    pointDrag={id:point.id,pointer:event.pointerId,start:screen,position:{lat:point.lat,lon:point.lon},moved:false,
               panEnabled:map.dragPan.isEnabled(),zoomEnabled:map.touchZoomRotate.isEnabled()};
    map.dragPan.disable();map.touchZoomRotate.disable();
    canvas.setPointerCapture(event.pointerId);
    canvas.style.cursor='grabbing';
    selectPlace(point,index);
  },true);
  canvas.addEventListener('pointermove',event=>{
    const screen=screenPoint(event);
    if(!pointDrag){canvas.style.cursor=hitPoint(screen)?'grab':mode==='route'?'crosshair':'';return;}
    if(event.pointerId!==pointDrag.pointer)return;
    if(!pointDrag.moved && Math.hypot(screen[0]-pointDrag.start[0],screen[1]-pointDrag.start[1])<4)return;
    event.preventDefault();
    pointDrag.moved=true;
    setFollow(false);wantReal=false;initialCentered=true;
    const position=map.unproject(screen);
    pointDrag.position={lat:Math.max(-85.051129,Math.min(85.051129,position.lat)),lon:((position.lng+180)%360+360)%360-180};
    // Preview once per frame; send a single route update when the pointer is released.
    if(pointDragFrame===null)pointDragFrame=requestAnimationFrame(()=>{
      pointDragFrame=null;
      if(!pointDrag)return;
      updateMapRoute();
      selected={...selected,...pointDrag.position};
      $("selected-coords").textContent=coords(selected);
      selectedMarker?.setLngLat(lnglat(selected));
    });
  });
  canvas.addEventListener('pointerup',safely(async event=>{
    if(event.pointerId!==pointDrag?.pointer)return;
    const drag=releasePointDrag();
    if(!drag.moved)return;
    try {
      await modifyRoute(value=>{
        const point=value.points.find(point=>point.id===drag.id);
        if(!point)throw new Error('이동할 지점이 삭제되었습니다');
        Object.assign(point,drag.position);
      });
    } finally {
      updateMapRoute();
      const index=route.points.findIndex(point=>point.id===drag.id);
      if(index>=0)selectPlace(route.points[index],index);
      else clearSelection();
    }
  }));
  canvas.addEventListener('pointercancel',cancelPointDrag);
  canvas.addEventListener('lostpointercapture',cancelPointDrag);
  canvas.addEventListener('pointerleave',()=>{if(!pointDrag)canvas.style.cursor=mode==='route'?'crosshair':'';});
  window.addEventListener('blur',cancelPointDrag);
  window.addEventListener('keydown',event=>{if(event.key==='Escape' && pointDrag){event.preventDefault();cancelPointDrag();}},true);
}
let bloomGrassImage;
async function installBloomTexture() {
  if (activeMapStyle !== "bloom" || !map.getLayer("bloom-grass-texture")) return;
  try {
    // Use a power-of-two texture for repeating patterns across Windows GPUs.
    if (!bloomGrassImage) bloomGrassImage = map.loadImage("/styles/bloom-grass.png").then(image => {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 512;
      const context = canvas.getContext("2d");
      context.drawImage(image.data, 0, 0, 512, 512);
      return context.getImageData(0, 0, 512, 512);
    });
    const texture = await bloomGrassImage;
    if (activeMapStyle !== "bloom" || !map.getLayer("bloom-grass-texture")) return;
    if (!map.hasImage("bloom-grass")) map.addImage("bloom-grass", texture);
    map.setPaintProperty("bloom-grass-texture", "background-pattern", "bloom-grass");
  } catch (error) {
    bloomGrassImage = null;
    console.warn("Grass texture unavailable", error);
  }
}
function installMapLayers() {
  installBloomTexture();
  const data={type:"FeatureCollection",features:[]};
  for(const id of ["bloom-route","bloom-points","bloom-return"])if(!map.getSource(id))map.addSource(id,{type:"geojson",data});
  const layers=[
    {id:"bloom-route-outline",type:"line",source:"bloom-route",paint:{"line-color":"#ffffff","line-width":7,"line-opacity":0.9},layout:{"line-cap":"round","line-join":"round"}},
    {id:"bloom-route-line",type:"line",source:"bloom-route",paint:{"line-color":"#138b82","line-width":3.5},layout:{"line-cap":"round","line-join":"round"}},
    {id:"bloom-return-line",type:"line",source:"bloom-return",paint:{"line-color":"#d1835c","line-width":2,"line-dasharray":[3,3]}},
    {id:"bloom-point-circles",type:"circle",source:"bloom-points",paint:{"circle-radius":["interpolate",["linear"],["zoom"],6,["max",4,["+",4,["*",2,["length",["get","label"]]]]],14,["max",9,["+",4,["*",2,["length",["get","label"]]]]]],"circle-color":"#ffffff","circle-stroke-color":"#138b82","circle-stroke-width":2}},
    {id:"bloom-point-labels",type:"symbol",source:"bloom-points",layout:{"text-field":["get","label"],"text-font":["Noto Sans Regular"],"text-size":11,"text-anchor":"center","text-offset":[0,0],"text-allow-overlap":true,"text-ignore-placement":true},paint:{"text-color":"#326961"}}
  ];
  layers.forEach(layer=>{if(!map.getLayer(layer.id))map.addLayer(layer);});
  mapReady=true;$("map-error").hidden=true;updateMapRoute();renderPositions();
}
function renderPositions() {
  if(!map || !state)return;
  const real=state.real_location;
  if(real && state.location_revision!==lastRealRevision) {
    lastRealRevision=state.location_revision;
    if(!realMarker){const element=document.createElement("div");element.className="real-marker";element.title="실제 위치";realMarker=new maplibregl.Marker({element}).setLngLat(lnglat(real)).addTo(map);}else realMarker.setLngLat(lnglat(real));
    const description=real.source==='Windows' ? `Windows 위치${real.accuracy != null ? ` · 오차 약 ${metres(real.accuracy)}`:''}` : "IP 추정 위치";
    $("map-position-label").textContent=description;
    $("real-location").title=`내 실제 위치로 지도 이동 · ${description}`;
    if(wantReal || !initialCentered) {centre(real,real.source==='IP'?13:16,!wantReal);wantReal=false;}
  }
  if(!real && !state.location_loading)$("map-position-label").textContent="실제 위치 확인 실패";
  $("real-location").classList.toggle("loading",state.location_loading);
  const position=state.position;
  if(position) {
    const point={lat:position[0],lon:position[1]};
    if(!simMarker){const element=document.createElement("div");element.className="sim-marker";element.innerHTML=glyph('navigation-2');element.title="모의 GPS";simMarker=new maplibregl.Marker({element}).setLngLat(lnglat(point)).addTo(map);icons();}else simMarker.setLngLat(lnglat(point));
    const stamp=position.join(',');
    if(stamp!==lastPosition && follow)map.easeTo({center:lnglat(point),duration:300});
    lastPosition=stamp;
    $("position-status").textContent=`모의 GPS ${coords(point)}${state.preview?' · 미리보기':''}`;
  } else {
    if(simMarker){simMarker.remove();simMarker=null;}
    lastPosition="";$("position-status").textContent=state.preview?"미리보기 · 기기 전송 없음":"모의 GPS 전송 없음";
  }
}
function renderState() {
  if(!state)return;
  const label=state.preview?"미리보기":state.connected?(state.connection.startsWith('Wi-Fi')?'Wi-Fi 연결됨':'USB 연결됨'):state.connection;
  $("connection-text").textContent=label;
  $("connection-button").title=state.connection;
  $("connection-dot").classList.toggle("connected",state.connected);
  $("heartbeat").checked=state.heartbeat;
  if(document.activeElement!==$("speed-input"))$("speed-input").value=state.speed;
  if(document.activeElement!==$("speed-slider")){$("speed-slider").max=Math.max(100,state.speed);$("speed-slider").value=state.speed;}
  $("play-route").innerHTML=glyph(state.running?'pause':'play')+`<span>${state.running?'일시 정지':state.paused?'이어서 걷기':'걷기 시작'}</span>`;
  $("play-route").disabled=!route?.points.length;
  const player=state.playback;
  $("playback-status").textContent=state.running?(player?.wait>0?`${player.wait.toFixed(1)}초 대기`:player?.phase==='forward'?`지점 ${(player?.index ?? 0)+1}로 이동 중`:player?.phase==='reverse'?'역순 복귀 중':'직선 복귀 중'):state.paused?'일시 정지':player?.finished?'이동 완료':'대기 중';
  $("cycle-status").textContent=player?`${player.completed}회 완료`:'';
  const progress=player?.finished?100:player?(Math.min(route.points.length-1,Math.max(0,player.index-1))/(Math.max(1,route.points.length-1))*100):0;
  $("progress-fill").style.width=`${progress}%`;
  $("log-connection").textContent=state.connection;
  const latest=state.logs.at(-1)?.id ?? 0;
  if(latest!==lastLog){lastLog=latest;$("device-logs").innerHTML=state.logs.length?state.logs.map(log=>`<div class="log-row"><time>${esc(log.time)}</time>${esc(log.message)}</div>`).join(''):'<div class="empty">연결 로그가 없습니다</div>';}
  renderPositions();icons();
}
async function searchPlaces() {
  if($("search-submit").disabled)return;
  const query=$("place-search").value.trim();if(!query)return;
  $("search-submit").disabled=true;$("search-results").hidden=false;$("close-search").hidden=false;
  $("search-results").innerHTML='<div class="search-note">검색 중...</div>';
  try {
    const result=await api(`search?q=${encodeURIComponent(query)}`);
    $("search-results").innerHTML=result.places.length?result.places.map((place,index)=>`<button class="search-result" data-place="${index}">${glyph('map-pin')}<div><strong>${esc(place.name)}</strong><small>${esc(place.address)}</small></div></button>`).join(''):'<div class="search-note">검색 결과가 없습니다</div>';
    $("search-results").querySelectorAll("button").forEach(button=>button.onclick=()=>{selectPlace(result.places[Number(button.dataset.place)],-1,true);$("search-results").hidden=true;});
    icons();
  } catch(error){$("search-results").innerHTML=`<div class="search-note">${esc(error.message)}</div>`;throw error;}
  finally{$("search-submit").disabled=false;}
}
async function sendKeys() {try{await action("keys",{keys:[...keys]});}catch(error){keys.clear();toast(error.message,true);}}
function releaseKeys() {if(keys.size){keys.clear();sendKeys();}clearInterval(keyTimer);keyTimer=null;}
let licenseCatalog;
async function showAbout() {
  releaseKeys();
  $("about-dialog").showModal();
  try {
    if(!licenseCatalog) licenseCatalog=fetch('/licenses/catalog.json').then(response=>{
      if(!response.ok)throw new Error('라이선스 파일을 불러오지 못했습니다');
      return response.json();
    });
    const catalog=await licenseCatalog;
    $("about-version").textContent=`버전 ${catalog.app_version}`;
    const groups={map:'지도 · 글꼴 · 검색',ui:'지도 렌더러 · 아이콘',runtime:'통신 · 실행 환경'};
    $("license-list").innerHTML=Object.entries(groups).map(([category,title])=>`<section class="license-group"><h4>${title}</h4>${catalog.entries.filter(item=>item.category===category).map(item=>`<details class="license-entry"><summary><span>${esc(item.name)}${item.version?` <small>${esc(item.version)}</small>`:''}</span><span class="license-label">${esc(item.license)}</span></summary><div class="license-content">${item.note?`<p>${esc(item.note)}</p>`:''}${/^https?:\/\//.test(item.url || '')?`<a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">공식 프로젝트</a>`:''}${item.documents.map(document=>`<h5>${esc(document.name)}</h5>${/^https?:\/\//.test(document.source)?`<a href="${esc(document.source)}" target="_blank" rel="noopener noreferrer">원문 출처</a>`:''}<pre tabindex="0">${esc(document.text)}</pre>`).join('')}</div></details>`).join('')}</section>`).join('');
    $("license-status").hidden=true;
  } catch(error) {
    licenseCatalog=null;
    $("license-status").hidden=false;
    $("license-status").textContent=error.message;
  }
}
function wireUI() {
  $("about-button").onclick=safely(showAbout);
  $("close-about").onclick=()=>$("about-dialog").close();
  $("editor-form").onsubmit=(event)=>{event.preventDefault();const fields=Object.fromEntries(new FormData(event.target));if(event.target.querySelector('input[name=name][required]') && !fields.name.trim()){$("dialog-error").textContent="이름을 입력하세요";$("dialog-error").hidden=false;return;}closeDialog(fields);};
  $("cancel-dialog").onclick=$("close-dialog").onclick=()=>closeDialog();
  $("editor-dialog").oncancel=(event)=>{event.preventDefault();closeDialog();};
  $("search-form").onsubmit=safely(event=>{event.preventDefault();return searchPlaces();});
  $("close-search").onclick=()=>{$("search-results").hidden=true;$("close-search").hidden=true;};
  $("close-place").onclick=clearSelection;
  $("add-place").onclick=safely(()=>selected && addPoint(selected));
  $("teleport-place").onclick=safely(async()=>{if(selected){setFollow(true);await action("teleport",{point:selected});}});
  $("coordinate-form").onsubmit=safely(async(event)=>{
    event.preventDefault();
    const point=parseCoordinates($("coordinate-input").value);
    $("coordinate-go").disabled=true;
    try {
      await action("teleport",{point});
      selectPlace(point);centre(point);setFollow(true);
      $("coordinate-input").value=coords(point);
      toast('입력한 좌표로 이동했습니다');
    } finally {$("coordinate-go").disabled=false;}
  });
  $("favorite-place").onclick=safely(favoritePlace);
  $("copy-coords").onclick=safely(async()=>{if(selected){await navigator.clipboard.writeText(coords(selected));toast("좌표를 복사했습니다");}});
  $("real-location").onclick=safely(async()=>{setFollow(false);if(state.real_location)centre(state.real_location,state.real_location.source==='IP'?13:16);wantReal=true;await action("locate");});
  $("sim-location").onclick=()=>{if(state.position)centre({lat:state.position[0],lon:state.position[1]});else toast("모의 GPS 위치가 없습니다");};
  $("fit-route").onclick=fitRoute;
  $("follow-map").onclick=()=>{setFollow(!follow);if(follow && state.position)map.easeTo({center:[state.position[1],state.position[0]],duration:500});};
  for(const value of ["select","route"])$("mode-"+value).onclick=()=>{mode=value;for(const id of ["select","route"]){$("mode-"+id).classList.toggle("active",id===mode);$("mode-"+id).setAttribute('aria-pressed',String(id===mode));}map.getCanvas().style.cursor=mode==='route'?'crosshair':'';};
  $("map-style").onchange=safely(()=>changeMapStyle($("map-style").value));
  $("retry-map").onclick=safely(()=>changeMapStyle(activeMapStyle,false));
  $("route-list").onscroll=renderList;
  new ResizeObserver(()=>{if(activeTab==='route')renderList();}).observe($("route-list"));
  document.querySelectorAll(".tab").forEach(button=>button.onclick=()=>setTab(button.dataset.tab));
  $("route-filter").oninput=$("place-filter").oninput=renderLibrary;
  $("rename-route").onclick=safely(async()=>{const result=await nameDialog("경로 이름 변경",route.name);if(result)await modifyRoute(value=>value.name=result.name.trim());});
  $("save-route").onclick=safely(saveRoute);
  $("new-route").onclick=safely(async()=>{if(!await confirmReplace())return;await enqueueRoute(async()=>{const result=await api("route",{route:emptyRoute(),revision});savedId=null;undoStack=[];redoStack=[];clearSelection();await acceptRoute(result,false);});setTab('route');});
  $("undo-route").onclick=safely(()=>historyStep(true));$("redo-route").onclick=safely(()=>historyStep(false));
  $("add-coordinate").onclick=safely(()=>editPoint());
  $("reverse-route").onclick=safely(()=>modifyRoute(value=>value.points.reverse()));
  $("clear-route").onclick=safely(async()=>{if(await confirmAction("경로 지우기",`${route.points.length}개 지점을 모두 지울까요?`,"지우기")){await modifyRoute(value=>value.points=[]);clearSelection();}});
  $("import-gpx").onclick=()=>$("gpx-file").click();
  $("gpx-file").onchange=safely(async()=>{const file=$("gpx-file").files[0];$("gpx-file").value='';if(!file)return;if(file.size>12*1024*1024)throw new Error("GPX 파일은 12MB 이하여야 합니다");if(!await confirmReplace())return;const text=await file.text();await enqueueRoute(async()=>{const result=await api("import",{gpx:text,revision});keepHistory(clone(route));redoStack=[];savedId=null;clearSelection();await acceptRoute(result);});setTab('route');fitRoute();toast(`${route.points.length}개 지점을 가져왔습니다`);});
  $("export-gpx").onclick=()=>{if(!route.points.length)return;const link=document.createElement('a');link.href='/api/export';link.download=route.name+'.gpx';document.body.append(link);link.click();link.remove();};
  $("repeat-route").onchange=safely(()=>modifyRoute(value=>value.repeat=$("repeat-route").checked));
  document.querySelectorAll("[data-return]").forEach(button=>button.onclick=safely(()=>modifyRoute(value=>value.return_mode=button.dataset.return)));
  $("repeat-count").onchange=safely(()=>modifyRoute(value=>value.repeat_count=$("repeat-count").value==='custom'?Number($("custom-count").value):Number($("repeat-count").value)));
  $("custom-count").onchange=safely(()=>modifyRoute(value=>value.repeat_count=Number($("custom-count").value)));
  $("return-speed").onchange=safely(()=>modifyRoute(value=>value.return_speed=Number($("return-speed").value)));
  let speedTimer;
  $("speed-slider").oninput=()=>{$("speed-input").value=$("speed-slider").value;clearTimeout(speedTimer);speedTimer=setTimeout(safely(async()=>{await action('speed',{value:Number($("speed-slider").value)});renderStats();renderList();}),150);};
  $("speed-input").onchange=safely(async()=>{await action('speed',{value:Number($("speed-input").value)});renderStats();renderList();});
  $("heartbeat").onchange=safely(()=>action('heartbeat',{value:$("heartbeat").checked}));
  $("play-route").onclick=safely(()=>{if(!state.running)setFollow(true);return action(state.running?'pause':'play');});
  $("stop-route").onclick=safely(()=>action('stop'));
  $("goto-start").onclick=safely(async()=>{if(route.points.length){setFollow(true);await action('teleport',{point:route.points[0]});}});
  $("restore-gps").onclick=safely(async()=>{if(await confirmAction("모의 GPS 해제","기기에 적용한 모의 위치를 해제하고 하트비트를 끌까요?","해제"))await action('restore');});
  $("connection-button").onclick=()=>{$("log-dialog").showModal();};$("close-log").onclick=()=>$("log-dialog").close();
  window.addEventListener('keydown',event=>{if(document.querySelector('dialog[open]') || event.target.closest('input,textarea,select,dialog') || event.ctrlKey || event.metaKey || event.altKey || !['w','a','s','d','arrowup','arrowdown','arrowleft','arrowright'].includes(event.key.toLowerCase()))return;event.preventDefault();if(!keys.has(event.key.toLowerCase())){keys.add(event.key.toLowerCase());sendKeys();if(!keyTimer)keyTimer=setInterval(sendKeys,500);}});
  window.addEventListener('keyup',event=>{if(keys.delete(event.key.toLowerCase())){sendKeys();if(!keys.size){clearInterval(keyTimer);keyTimer=null;}}});
  window.addEventListener('blur',releaseKeys);document.addEventListener('visibilitychange',()=>{if(document.hidden)releaseKeys();});
}
let map;
async function poll() {
  try {
    state=await api('state');
    if(state.route_revision!==revision){const result=await api('route');await acceptRoute(result);}
    renderState();
  } catch(error){$("connection-text").textContent="앱 연결 끊김";$("connection-dot").classList.remove('connected');}
  setTimeout(poll,document.hidden?1000:300);
}
async function initialize() {
  const data=await api('bootstrap');token=data.token;state=data.state;route=data.route;revision=data.revision;library=data.library;
  state.length_m=data.length_m;
  const requestedStyle=new URLSearchParams(location.search).get('style');
  const savedStyle=library.preferences?.map_style;
  activeMapStyle=mapStyles.has(requestedStyle)?requestedStyle:mapStyles.has(savedStyle)?savedStyle:'osm';
  wireUI();renderRoute();renderLibrary();icons();
  $("map-style").value=activeMapStyle;
  try {
    map=new maplibregl.Map({container:'map',style:mapStyle(activeMapStyle),center:[0,20],zoom:1.5,maxPitch:0,dragRotate:false,touchPitch:false,keyboard:false,attributionControl:{compact:false},canvasContextAttributes:{preserveDrawingBuffer:new URLSearchParams(location.search).has('qa')}});
    map.touchZoomRotate.disableRotation();
    wirePointDragging();
    window.bloomMap=map;
    map.addControl(new maplibregl.NavigationControl({showCompass:false}),'bottom-right');
    map.on('style.load',installMapLayers);
    map.on('dragstart',()=>{setFollow(false);wantReal=false;initialCentered=true;});
    map.on('click',safely(async event=>{
      if(performance.now()<ignoreMapClickUntil)return;
      const features=map.queryRenderedFeatures(event.point,{layers:map.getLayer('bloom-point-circles')?['bloom-point-circles']:[]});
      if(features.length){const index=Number(features[0].properties.index);selectPlace(route.points[index],index);return;}
      const nearby=map.queryRenderedFeatures(event.point).find(x=>x.properties?.name);
      const point={lat:event.lngLat.lat,lon:event.lngLat.lng,name:nearby?.properties.name || ''};
      if(mode==='route')await addPoint(point);else selectPlace(point);
    }));
    map.on('contextmenu',event=>{event.originalEvent.preventDefault();selectPlace({lat:event.lngLat.lat,lon:event.lngLat.lng,name:''});});
    map.getCanvas().addEventListener('mousedown',safely(async event=>{if(event.button===1){event.preventDefault();const rect=map.getCanvas().getBoundingClientRect(),point=map.unproject([event.clientX-rect.left,event.clientY-rect.top]);await addPoint({lat:point.lat,lon:point.lng,name:''});}}));
    map.getCanvas().addEventListener('auxclick',event=>{if(event.button===1)event.preventDefault();});
    map.on('error',()=>{if(!mapReady)$("map-error").hidden=false;});
    setTimeout(()=>{if(!mapReady)$("map-error").hidden=false;},18000);
  } catch(error){$("map-error").hidden=false;toast(`지도 초기화 실패: ${error.message}`,true);}
  renderState();poll();
}
initialize().catch(error=>toast(`앱 초기화 실패: ${error.message}`,true));
