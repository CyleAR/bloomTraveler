import time
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

def set_fake_location(latitude, longitude):
    try:
        # 1. USB로 연결된 기기 인식 및 잠금 해제 세션 생성
        lockdown = create_using_usbmux()
        
        # 2. 위치 시뮬레이션 서비스 시작
        print("위치 시뮬레이션 서비스를 시작합니다...")
        location_service = LocationSimulation(lockdown)
        
        # 3. 좌표 주입
        print(f"좌표 전송 중: 위도 {latitude}, 경도 {longitude}")
        location_service.set(latitude, longitude)
        print("위치가 성공적으로 변경되었습니다!")
        
        # 위치 유지 (필요에 따라 조절)
        time.sleep(2)
        
    except Exception as e:
        print(f"오류 발생: {e}")
        print("기기 화면이 잠겨있지 않은지, '이 컴퓨터를 신뢰함'을 눌렀는지 확인하세요.")

# 테스트 좌표: 서울 남산타워 (위도: 37.5511, 경도: 126.9882)
if __name__ == "__main__":
    set_fake_location(37.5511, 126.9882)