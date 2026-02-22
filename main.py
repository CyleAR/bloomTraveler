import customtkinter
import tkintermapview
import subprocess
import threading
import time
import urllib.request
import json
import math
import os
import sys
import multiprocessing
import ctypes
from PIL import Image, ImageDraw, ImageTk
import tkinter as tk
from tkinter import messagebox 

# ==========================================
# 🚀 내장 CLI 라우터 (exe 파일 내에서 pymobiledevice3 명령어 실행)
# 콘솔 창을 살렸으므로, 이제 아무런 땜질 없이 순정으로 실행됩니다!
# ==========================================
if len(sys.argv) > 1 and sys.argv[1] == "internal_pm3":
    sys.argv = ["pymobiledevice3"] + sys.argv[2:]
    from pymobiledevice3.__main__ import main as pm3_main
    try: pm3_main()
    except SystemExit: pass
    sys.exit(0)

def get_pm3_cmd(args_str):
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}" internal_pm3 {args_str}'
    else:
        return f'pymobiledevice3 {args_str}'

def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()
    except: return False

# ==========================================
# ⚙️ 유틸리티 함수들
# ==========================================
def get_real_location():
    try:
        with urllib.request.urlopen("http://ip-api.com/json/", timeout=3) as response:
            data = json.loads(response.read().decode())
            return data['lat'], data['lon']
    except Exception:
        return 37.5665, 126.9780

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371.0 
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def make_circle_icon(color, size=24):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, size-2, size-2), fill=color, outline="white", width=2)
    return ImageTk.PhotoImage(img)

# ==========================================
# 🧠 코어 동작 함수들 
# ==========================================
def show_disconnect_warning():
    messagebox.showwarning("기기 연결 오류", "아이패드(또는 아이폰)와의 연결이 끊어졌거나 인식할 수 없습니다.\n케이블 및 '신뢰함' 여부를 확인하세요.")

def connection_monitor():
    global device_connected, already_warned, is_moving
    while True:
        try:
            cmd = get_pm3_cmd("usbmux list")
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=2)
            status = "Identifier" in result.stdout
            if status is False:
                if device_connected is not False:
                    device_connected = False; is_moving = False 
                    root.after(0, lambda: conn_status_label.configure(text="🔴 기기 연결 끊김", text_color="#E57373"))
                    if not already_warned:
                        already_warned = True; root.after(0, show_disconnect_warning)
            else:
                if device_connected is not True:
                    device_connected = True; already_warned = False
                    root.after(0, lambda: conn_status_label.configure(text="🟢 기기 정상 연결됨", text_color="#81C784"))
        except:
            if device_connected is not False:
                device_connected = False; is_moving = False
                root.after(0, lambda: conn_status_label.configure(text="🔴 기기 연결 끊김", text_color="#E57373"))
                if not already_warned:
                    already_warned = True; root.after(0, show_disconnect_warning)
        time.sleep(2) 

def run_command_sync(lat, lng):
    if not device_connected: return 
    cmd = get_pm3_cmd(f"developer dvt simulate-location set {lat} {lng}")
    # 창 숨김 옵션을 모두 뺐습니다. 이제 메인 콘솔 창에 로그가 자연스럽게 찍힙니다!
    subprocess.run(cmd, shell=True, check=False)

def update_current_location(lat, lng, move_map=False):
    global current_lat, current_lng, my_marker
    current_lat, current_lng = lat, lng
    if my_marker is None: my_marker = map_widget.set_marker(lat, lng, icon=icon_me)
    else: my_marker.set_position(lat, lng)
    if move_map: map_widget.set_position(lat, lng)
    status_label.configure(text=f"현재 위치:\n{lat:.5f}, {lng:.5f}")
    threading.Thread(target=run_command_sync, args=(lat, lng), daemon=True).start()

def update_path():
    global path_line
    if path_line: path_line.delete(); path_line = None
    all_points = [(current_lat, current_lng)] + waypoint_list
    if target_coords: all_points.append(target_coords)
    if len(all_points) > 1: path_line = map_widget.set_path(all_points, color="#64B5F6", width=3)

def map_left_click(coords):
    global target_coords, target_marker
    target_coords = coords
    if target_marker is None: target_marker = map_widget.set_marker(coords[0], coords[1], icon=icon_target)
    else: target_marker.set_position(coords[0], coords[1])
    target_label.configure(text=f"목적지:\n{coords[0]:.5f}, {coords[1]:.5f}")
    update_path() 

def map_middle_click(event):
    lat, lng = map_widget.convert_canvas_coords_to_decimal_coords(map_widget.canvas.canvasx(event.x), map_widget.canvas.canvasy(event.y))
    if len(waypoint_list) >= 15: return
    waypoint_list.append((lat, lng))
    waypoint_markers.append(map_widget.set_marker(lat, lng, icon=icon_waypoint))
    update_path() 

def btn_go_to_coords():
    try:
        parts = [p.strip() for p in entry_coords.get().split(',')]
        if len(parts) != 2: raise ValueError
        lat, lng = float(parts[0]), float(parts[1])
        map_left_click((lat, lng)); update_current_location(lat, lng, move_map=True) 
    except ValueError: pass

def btn_teleport():
    if target_coords: update_current_location(target_coords[0], target_coords[1])

def btn_walk():
    global is_moving
    if not target_coords and not waypoint_list: return
    if is_moving: return
    if not device_connected: show_disconnect_warning(); return
    speed_kmh = speed_slider.get()
    if speed_kmh <= 0: return
    is_moving = True
    
    def walk_task():
        global is_moving
        path_to_walk = waypoint_list.copy()
        if target_coords: path_to_walk.append(target_coords)
        completed = True 
        
        for point in path_to_walk:
            if not is_moving or not device_connected: completed = False; break
            start_lat, start_lng = current_lat, current_lng
            end_lat, end_lng = point
            dist_km = haversine_distance(start_lat, start_lng, end_lat, end_lng)
            if dist_km == 0: continue
            steps = max(int((dist_km / speed_kmh) * 3600), 1)
            for i in range(1, steps + 1):
                if not is_moving or not device_connected: completed = False; break
                t = i / steps
                update_current_location(start_lat + (end_lat - start_lat) * t, start_lng + (end_lng - start_lng) * t)
                time.sleep(1.0) 
        is_moving = False
        if completed: root.after(0, btn_clear_waypoints)
    threading.Thread(target=walk_task, daemon=True).start()

def btn_clear_waypoints():
    global waypoint_list, waypoint_markers
    for m in waypoint_markers: m.delete()
    waypoint_markers.clear(); waypoint_list.clear()
    update_path() 

def btn_clear_all():
    global is_moving, target_coords, target_marker, path_line
    is_moving = False
    if target_marker: target_marker.delete(); target_marker = None
    target_coords = None
    for m in waypoint_markers: m.delete()
    waypoint_markers.clear(); waypoint_list.clear()
    if path_line: path_line.delete(); path_line = None
    def task():
        if device_connected: subprocess.run(get_pm3_cmd("developer dvt simulate-location clear"), shell=True)
    threading.Thread(target=task, daemon=True).start()
    status_label.configure(text="현재 위치:\n실제 위치로 복구됨")
    target_label.configure(text="목적지:\n지도 클릭 또는 직접 입력")

# ==========================================
# 🖥️ 메인 실행 블록
# ==========================================
if __name__ == '__main__':
    multiprocessing.freeze_support()
    
    if not is_admin():
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
        sys.exit(0)
        
    print("🚀 백그라운드 터널링 시작 중...")
    tunnel_process = subprocess.Popen(get_pm3_cmd("remote tunneld"), shell=True)

    customtkinter.set_appearance_mode("Dark")
    root = customtkinter.CTk()
    root.geometry("1050x760") 
    root.title("iOS GPS Spoofer Pro - Master Edition")

    icon_me = make_circle_icon("#1976D2", 20)     
    icon_target = make_circle_icon("#D32F2F", 20) 
    icon_waypoint = make_circle_icon("#FBC02D", 16) 
    current_lat, current_lng = get_real_location()
    target_coords, is_moving, my_marker, target_marker = None, False, None, None
    waypoint_list, waypoint_markers, path_line = [], [], None
    device_connected, already_warned = None, False

    root.grid_columnconfigure(0, weight=1)
    root.grid_columnconfigure(1, weight=0)
    root.grid_rowconfigure(0, weight=1)

    map_frame = customtkinter.CTkFrame(root)
    map_frame.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
    map_widget = tkintermapview.TkinterMapView(map_frame, corner_radius=10)
    map_widget.pack(fill="both", expand=True)

    map_widget.add_left_click_map_command(map_left_click)
    map_widget.canvas.bind("<Button-2>", map_middle_click) 

    def custom_right_click(event):
        lat, lng = map_widget.convert_canvas_coords_to_decimal_coords(map_widget.canvas.canvasx(event.x), map_widget.canvas.canvasy(event.y))
        coord_str = f"{lat:.6f}, {lng:.6f}"
        def copy_silently():
            root.clipboard_clear(); root.clipboard_append(coord_str)
        menu = tk.Menu(root, tearoff=0, font=("Arial", 10))
        menu.add_command(label=f"📋 좌표 복사 ({coord_str})", command=copy_silently)
        menu.add_separator()
        menu.add_command(label="📍 여기를 목적지로 핀 꽂기", command=lambda: map_left_click((lat, lng)))
        menu.add_command(label="🚀 여기로 즉시 순간이동", command=lambda: update_current_location(lat, lng))
        menu.tk_popup(event.x_root, event.y_root)

    map_widget.canvas.bind("<Button-3>", custom_right_click)

    control_frame = customtkinter.CTkFrame(root, width=260)
    control_frame.grid(row=0, column=1, sticky="ns", padx=(5, 10), pady=10)
    control_frame.grid_propagate(False)

    customtkinter.CTkLabel(control_frame, text="GPS 제어 패널", font=("Arial", 18, "bold")).pack(pady=(15, 5))
    conn_status_label = customtkinter.CTkLabel(control_frame, text="⏳ 연결 상태 확인 중...", text_color="#FFB74D", font=("Arial", 12, "bold"))
    conn_status_label.pack(pady=(0, 10))
    status_label = customtkinter.CTkLabel(control_frame, text="현재 위치:\n대기 중... (이동을 시작하세요)", text_color="#64B5F6")
    status_label.pack(pady=5)
    target_label = customtkinter.CTkLabel(control_frame, text="목적지:\n지도 클릭 또는 직접 입력", text_color="#E57373")
    target_label.pack(pady=5)
    customtkinter.CTkLabel(control_frame, text="💡 휠 클릭: 경유지 추가", text_color="gray", font=("Arial", 11)).pack()

    input_frame = customtkinter.CTkFrame(control_frame, fg_color="transparent")
    input_frame.pack(pady=5, padx=10, fill="x")
    entry_coords = customtkinter.CTkEntry(input_frame, placeholder_text="위도, 경도 (예: 37.50, 126.87)", height=30)
    entry_coords.pack(pady=5, fill="x")
    customtkinter.CTkButton(input_frame, text="좌표로 이동", command=btn_go_to_coords, fg_color="#546E7A", hover_color="#455A64").pack(pady=5, fill="x")

    customtkinter.CTkLabel(control_frame, text="이동 속도:").pack(pady=(10, 0))
    speed_val_label = customtkinter.CTkLabel(control_frame, text="15.0 km/h", text_color="#81C784", font=("Arial", 12, "bold"))
    speed_val_label.pack()
    speed_slider = customtkinter.CTkSlider(control_frame, from_=0, to=50, number_of_steps=500, command=lambda val: speed_val_label.configure(text=f"{val:.1f} km/h"))
    speed_slider.set(15.0)
    speed_slider.pack(pady=5, padx=10)

    customtkinter.CTkButton(control_frame, text="🚀 순간이동", command=btn_teleport, fg_color="#1976D2").pack(pady=5, padx=10, fill="x")
    customtkinter.CTkButton(control_frame, text="🚶‍♂️ 걷기 시작", command=btn_walk, fg_color="#388E3C").pack(pady=5, padx=10, fill="x")
    customtkinter.CTkButton(control_frame, text="🛑 정지", command=lambda: globals().update(is_moving=False), fg_color="#F57C00").pack(pady=5, padx=10, fill="x")
    customtkinter.CTkButton(control_frame, text="🗑️ 경유지 모두 지우기", command=btn_clear_waypoints, fg_color="#5D4037", hover_color="#4E342E").pack(pady=5, padx=10, fill="x")
    customtkinter.CTkButton(control_frame, text="🔄 원래 위치 복구", command=btn_clear_all, fg_color="#C62828").pack(pady=(15, 10), padx=10, fill="x")

    map_widget.set_position(current_lat, current_lng)
    map_widget.set_zoom(15)
    map_left_click((current_lat, current_lng))

    threading.Thread(target=connection_monitor, daemon=True).start()

    try:
        root.mainloop()
    finally:
        print("🛑 프로그램을 종료합니다. 터널을 닫는 중...")
        tunnel_process.kill()