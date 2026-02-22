import customtkinter
import tkintermapview
import subprocess
import threading
import time
import urllib.request
import json
import math
import os
from PIL import Image, ImageDraw, ImageTk
import tkinter as tk
from tkinter import messagebox # 팝업 알림창용

# --- 윈도우 CMD 창 깜빡임 방지 옵션 ---
CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

# --- 🌐 IP 기반 현재 위치 가져오기 ---
def get_real_location():
    try:
        with urllib.request.urlopen("http://ip-api.com/json/", timeout=3) as response:
            data = json.loads(response.read().decode())
            return data['lat'], data['lon']
    except Exception:
        return 37.5665, 126.9780

# --- 📐 거리 계산 (하버사인 공식) ---
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371.0 
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

# UI 기본 설정
customtkinter.set_appearance_mode("Dark")
root = customtkinter.CTk()
root.geometry("1050x700") 
root.title("iOS GPS Spoofer Pro - Master Edition")

# --- 🎨 커스텀 마커 이미지 생성 ---
def make_circle_icon(color, size=24):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, size-2, size-2), fill=color, outline="white", width=2)
    return ImageTk.PhotoImage(img)

icon_me = make_circle_icon("#1976D2", 20)     
icon_target = make_circle_icon("#D32F2F", 20) 

# 상태 변수
current_lat, current_lng = get_real_location()
target_coords = None
is_moving = False
my_marker = None
target_marker = None

# 기기 연결 상태 추적 변수
device_connected = None 
already_warned = False

# ----------------- 🚨 기기 연결 모니터링 로직 -----------------

def show_disconnect_warning():
    """연결 끊김 팝업창 띄우기 (UI 스레드에서 실행)"""
    messagebox.showwarning(
        "기기 연결 오류", 
        "아이패드(또는 아이폰)와의 연결이 끊어졌거나 인식할 수 없습니다.\n\n"
        "1. 케이블 연결 상태를 확인하세요.\n"
        "2. 기기에서 '이 컴퓨터를 신뢰함'을 눌렀는지 확인하세요.\n"
        "3. tunneld 데몬이 실행 중인지 확인하세요."
    )

def connection_monitor():
    """실제로 기기에 '말을 걸어' 연결 상태를 확인하는 강화된 모니터링"""
    global device_connected, already_warned, is_moving
    
    while True:
        try:
            # 단순히 list만 보는 게 아니라, 실제 기기의 식별자(UDID)를 가져오려고 시도
            # 연결이 끊기면 이 명령어가 즉시 실패하거나 빈 값을 내뱉습니다.
            result = subprocess.run(
                "pymobiledevice3 usbmux list", 
                capture_output=True, text=True, 
                creationflags=CREATE_NO_WINDOW,
                timeout=2 # 2초 안에 응답 없으면 끊긴 걸로 간주
            )
            
            # 출력 내용이 [] 이거나 기기 정보가 포함되어 있지 않으면 끊긴 것
            if "Identifier" not in result.stdout:
                status = False
            else:
                status = True

            if status is False:
                if device_connected is not False:
                    device_connected = False
                    is_moving = False # 걷기 즉시 중지
                    root.after(0, lambda: conn_status_label.configure(text="🔴 기기 연결 끊김", text_color="#E57373"))
                    
                    if not already_warned:
                        already_warned = True
                        root.after(0, show_disconnect_warning)
            else:
                if device_connected is not True:
                    device_connected = True
                    already_warned = False
                    root.after(0, lambda: conn_status_label.configure(text="🟢 기기 정상 연결됨", text_color="#81C784"))
                    
        except (subprocess.TimeoutExpired, Exception):
            # 타임아웃이 발생했다는 건 응답을 못 할 정도로 끊겼다는 뜻
            if device_connected is not False:
                device_connected = False
                is_moving = False
                root.after(0, lambda: conn_status_label.configure(text="🔴 기기 연결 끊김", text_color="#E57373"))
                if not already_warned:
                    already_warned = True
                    root.after(0, show_disconnect_warning)
            
        time.sleep(2) # 체크 주기를 2초로 단축
        
# 모니터링 스레드 시작
threading.Thread(target=connection_monitor, daemon=True).start()

# ----------------- 코어 로직 -----------------

def run_command_sync(lat, lng):
    if not device_connected: return # 연결 안 되어 있으면 전송 안 함
    
    command = f"pymobiledevice3 developer dvt simulate-location set {lat} {lng}"
    try:
        subprocess.run(command, shell=True, check=False, creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        print(f"❌ 전송 에러: {e}")

def update_current_location(lat, lng, move_map=False):
    global current_lat, current_lng, my_marker
    current_lat, current_lng = lat, lng
    
    if my_marker is None:
        my_marker = map_widget.set_marker(lat, lng, icon=icon_me)
    else:
        my_marker.set_position(lat, lng)
        
    if move_map:
        map_widget.set_position(lat, lng)
        
    status_label.configure(text=f"현재 위치:\n{lat:.5f}, {lng:.5f}")
    threading.Thread(target=run_command_sync, args=(lat, lng), daemon=True).start()

def map_left_click(coords):
    global target_coords, target_marker
    target_coords = coords
    lat, lng = coords
    
    if target_marker is None:
        target_marker = map_widget.set_marker(lat, lng, icon=icon_target)
    else:
        target_marker.set_position(lat, lng)
        
    target_label.configure(text=f"목적지:\n{lat:.5f}, {lng:.5f}")

# ----------------- 좌표 입력 이동 -----------------

def btn_go_to_coords():
    coords_str = entry_coords.get()
    try:
        parts = [p.strip() for p in coords_str.split(',')]
        if len(parts) != 2:
            raise ValueError
            
        lat = float(parts[0])
        lng = float(parts[1])
        
        map_left_click((lat, lng)) 
        update_current_location(lat, lng, move_map=True) 
    except ValueError:
        print("❌ 잘못된 좌표 형식입니다. '위도, 경도' (예: 37.50, 126.87) 형식으로 입력하세요.")

# ----------------- 이동 로직 -----------------

def btn_teleport():
    if not target_coords: return
    update_current_location(target_coords[0], target_coords[1])

def btn_walk():
    global is_moving
    if not target_coords or is_moving: return
    
    if not device_connected:
        show_disconnect_warning()
        return
        
    speed_kmh = speed_slider.get()
    if speed_kmh <= 0: return
        
    is_moving = True
    
    def walk_task():
        global is_moving
        start_lat, start_lng = current_lat, current_lng
        end_lat, end_lng = target_coords
        dist_km = haversine_distance(start_lat, start_lng, end_lat, end_lng)
        
        if dist_km == 0:
            is_moving = False
            return
            
        total_seconds = (dist_km / speed_kmh) * 3600
        tick_rate = 1.0 
        steps = max(int(total_seconds / tick_rate), 1)
        
        for i in range(1, steps + 1):
            if not is_moving or not device_connected: break
            t = i / steps
            update_current_location(start_lat + (end_lat - start_lat) * t, 
                                    start_lng + (end_lng - start_lng) * t)
            time.sleep(tick_rate) 
        is_moving = False

    threading.Thread(target=walk_task, daemon=True).start()

def btn_clear():
    global is_moving
    is_moving = False
    
    def task():
        if device_connected:
            subprocess.run("pymobiledevice3 developer dvt simulate-location clear", shell=True, creationflags=CREATE_NO_WINDOW)
            
    threading.Thread(target=task, daemon=True).start()
    status_label.configure(text="현재 위치:\n실제 위치로 복구됨")

# ----------------- UI 레이아웃 -----------------

root.grid_columnconfigure(0, weight=1)
root.grid_columnconfigure(1, weight=0)
root.grid_rowconfigure(0, weight=1)

# 좌측 지도
map_frame = customtkinter.CTkFrame(root)
map_frame.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
map_widget = tkintermapview.TkinterMapView(map_frame, corner_radius=10)
map_widget.pack(fill="both", expand=True)
map_widget.add_left_click_map_command(map_left_click)

# 커스텀 우클릭 메뉴
def custom_right_click(event):
    canvas_x = map_widget.canvas.canvasx(event.x)
    canvas_y = map_widget.canvas.canvasy(event.y)
    lat, lng = map_widget.convert_canvas_coords_to_decimal_coords(canvas_x, canvas_y)
    coord_str = f"{lat:.6f}, {lng:.6f}"
    
    def copy_silently():
        root.clipboard_clear()
        root.clipboard_append(coord_str)
        
    menu = tk.Menu(root, tearoff=0, font=("Arial", 10))
    menu.add_command(label=f"📋 좌표 복사 ({coord_str})", command=copy_silently)
    menu.add_separator()
    menu.add_command(label="📍 여기를 목적지로 핀 꽂기", command=lambda: map_left_click((lat, lng)))
    menu.add_command(label="🚀 여기로 즉시 순간이동", command=lambda: update_current_location(lat, lng))
    menu.tk_popup(event.x_root, event.y_root)

map_widget.canvas.bind("<Button-3>", custom_right_click)

# 우측 패널
control_frame = customtkinter.CTkFrame(root, width=260)
control_frame.grid(row=0, column=1, sticky="ns", padx=(5, 10), pady=10)
control_frame.grid_propagate(False)

customtkinter.CTkLabel(control_frame, text="GPS 제어 패널", font=("Arial", 18, "bold")).pack(pady=(15, 5))

# ⭐ 연결 상태 표시 라벨 (패널 맨 위)
conn_status_label = customtkinter.CTkLabel(control_frame, text="⏳ 연결 상태 확인 중...", text_color="#FFB74D", font=("Arial", 12, "bold"))
conn_status_label.pack(pady=(0, 10))

status_label = customtkinter.CTkLabel(control_frame, text="현재 위치:\n대기 중... (이동을 시작하세요)", text_color="#64B5F6")
status_label.pack(pady=5)

target_label = customtkinter.CTkLabel(control_frame, text="목적지:\n지도 클릭 또는 직접 입력", text_color="#E57373")
target_label.pack(pady=5)

# --- ⌨️ 좌표 입력 섹션 ---
input_frame = customtkinter.CTkFrame(control_frame, fg_color="transparent")
input_frame.pack(pady=10, padx=10, fill="x")

entry_coords = customtkinter.CTkEntry(input_frame, placeholder_text="위도, 경도 (예: 37.50, 126.87)", height=30)
entry_coords.pack(pady=5, fill="x")

go_btn = customtkinter.CTkButton(input_frame, text="좌표로 이동", command=btn_go_to_coords, fg_color="#546E7A", hover_color="#455A64")
go_btn.pack(pady=5, fill="x")

# --- ⚡ 속도 및 조작 섹션 ---
customtkinter.CTkLabel(control_frame, text="이동 속도:").pack(pady=(15, 0))
speed_val_label = customtkinter.CTkLabel(control_frame, text="15.0 km/h", text_color="#81C784", font=("Arial", 12, "bold"))
speed_val_label.pack()

def update_speed_label(val): 
    speed_val_label.configure(text=f"{val:.1f} km/h")
    
speed_slider = customtkinter.CTkSlider(control_frame, from_=0, to=50, number_of_steps=500, command=update_speed_label)
speed_slider.set(15.0)
speed_slider.pack(pady=5, padx=10)

teleport_btn = customtkinter.CTkButton(control_frame, text="🚀 순간이동", command=btn_teleport, fg_color="#1976D2")
teleport_btn.pack(pady=5, padx=10, fill="x")

walk_btn = customtkinter.CTkButton(control_frame, text="🚶‍♂️ 걷기 시작", command=btn_walk, fg_color="#388E3C")
walk_btn.pack(pady=5, padx=10, fill="x")

stop_btn = customtkinter.CTkButton(control_frame, text="🛑 정지", command=lambda: globals().update(is_moving=False), fg_color="#F57C00")
stop_btn.pack(pady=5, padx=10, fill="x")

clear_btn = customtkinter.CTkButton(control_frame, text="🔄 원래 위치 복구", command=btn_clear, fg_color="#C62828")
clear_btn.pack(pady=(20, 10), padx=10, fill="x")

map_widget.set_position(current_lat, current_lng)
map_widget.set_zoom(15)
map_left_click((current_lat, current_lng))

root.mainloop()