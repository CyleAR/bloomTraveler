# Bloom Traveler

탈옥하지 않은 iPhone·iPad의 GPS 위치를 변경하는 Windows 앱입니다.

## 주요 기능

- 지도·좌표·장소 검색으로 순간이동, WASD·방향키로 수동 이동
- 경로 편집 및 자동 걷기, 지점 드래그와 걷는 중 수정
- 지점별 속도·대기 설정, 경로 반복과 복귀 방식 선택
- GPX 가져오기·내보내기, 이름 지정 경로 저장, 장소 즐겨찾기
- USB·Wi-Fi 자동 연결, 유휴 상태에서도 위치를 유지하는 하트비트
- 기본 지도·심플 지도 선택, 모의 GPS 해제

## 설치 및 사용

1. [릴리즈](https://github.com/CyleAR/bloomTraveler/releases)에서 압축 파일을 받아 **전체를 풀고** `Bloom Traveler.exe`를 실행합니다. EXE만 따로 옮기면 실행되지 않습니다.
2. 처음에는 기기를 USB로 연결하고 잠금을 푼 뒤 **신뢰함**을 승인합니다.
3. 장소 검색이나 좌표 입력으로 이동하거나, 지도에서 지점을 추가해 **걷기 시작**을 누릅니다.
4. Wi-Fi는 최초 USB 페어링 후 PC와 기기를 같은 네트워크에 두고 사용합니다.

필요 환경: Windows 10/11, [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/), Apple Devices 또는 iTunes의 기기 드라이버, 기기의 개발자 모드. 지도와 검색에는 인터넷이 필요합니다. 배포본은 Python 설치가 필요 없습니다.

iOS 17.0~17.3의 USB 터널 연결은 지원하지 않습니다. iOS 17.4 이상을 권장합니다.

## 알아둘 점

- 새 경로 걷기는 출발점으로 순간이동한 뒤 시작합니다. 일시 정지 후에는 멈춘 위치에서 이어갑니다.
- 지도에서 번호 지점을 끌어 위치를 바꿀 수 있습니다. 걷는 중에도 반영되며 실행 취소가 가능합니다.
- **실제 위치** 버튼은 지도만 이동합니다. PC의 Windows 위치 또는 IP 추정값이며, 기기 GPS와 다를 수 있습니다.
- **기기의 모의 GPS 해제**는 위치 시뮬레이션과 하트비트를 중지합니다.
- 앱은 한 번에 하나만 실행하세요. 연결 오류는 상단 연결 상태를 눌러 확인합니다.

저장 데이터: `%LOCALAPPDATA%\BloomTraveler\library.json`  
연결 로그: `%LOCALAPPDATA%\BloomTraveler\device.log`

## 소스 실행 및 빌드

Python 3.10 기준입니다. 프로젝트 폴더에서 PowerShell로 실행합니다.

```powershell
chcp 65001
$env:PYTHONUTF8 = '1'
python -m venv .venv-modern
.\.venv-modern\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.\.venv-modern\Scripts\python.exe main.py
```

빌드:

```powershell
chcp 65001
$env:PYTHONUTF8 = '1'
.\.venv-modern\Scripts\python.exe -m PyInstaller --noconfirm "Bloom Traveler.spec"
```

배포할 폴더는 `dist\Bloom Traveler`입니다. `build`는 중간 산출물입니다.

테스트는 `.venv-modern\Scripts\python.exe -m unittest discover -s tests -v`로 실행합니다. 기기 없이 실행하려면 `main.py --browser --preview --port 8840`을 사용합니다.

## 자동 배포

`main.py`의 `VERSION`을 올리고 `main` 또는 `master`에 푸시하면 GitHub Actions가 Windows 빌드 → `.7z` 압축 → 버전 태그·릴리즈 생성을 처리합니다. 이미 배포한 버전은 건너뜁니다.

`v2.0.6`처럼 버전과 일치하는 태그를 푸시하거나 Actions의 **Windows Release → Run workflow**로도 실행할 수 있습니다. 별도 토큰 설정은 필요 없습니다.

## 주의 사항

교육·개발 테스트용 프로그램입니다. 위치 기반 서비스의 약관 위반이나 계정 제재에 대한 책임은 사용자에게 있습니다.

## 작동 확인 기기

iPhone 15 Pro (iOS 26), iPad Air 4 (iPadOS 26.2), iPhone 17 (iOS 26.3), iPhone 12 mini (iOS 18.5)

Thanks: reathena, oob

