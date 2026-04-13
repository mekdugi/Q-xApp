# Q-xApp Project Instructions

## 프로젝트 시작 시 반드시 읽을 것
이 프로젝트의 메모리에 `handover_log.md`가 있다. 이전 세션에서 수행한 **모든 코드 수정, 버그 수정, 검증 결과, 앞으로 할 일**이 상세히 기록되어 있으니 반드시 참고할 것.

## 핵심 정보
- 작업 환경: Windows 10 + WSL2 Ubuntu 24.04
- WSL 명령은 `wsl bash -c "..."` 형태로 실행
- FlexRIC/Q-xApp은 `/root/` 하위 → sudo 필요
- ns-3는 `/home/wookjin/` 하위 → sudo 없이 wookjin 유저로 실행
- GUI는 Docker 컨테이너 (gui-gui-1, gui-influxdb-1, gui-grafana-1)
- 이 폴더(Q-xApp)는 소스 코드 로컬 사본. 실제 실행 파일은 WSL 안에 있음.
- GitHub repo: https://github.com/mekdugi/Q-xApp

## 사용자 선호
- 한국어로 대화
- 간결하게, 핵심만
- 코드 수정 시 바로 빌드까지 해줄 것
