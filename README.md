# KAIST Study MCP

각 사용자의 PC에서 실행되는 읽기 전용 KAIST MCP 서버입니다. 사용자는 전용 브라우저에서 자신의 KAIST 계정으로 직접 로그인합니다. 비밀번호·OTP를 MCP 대화에 입력하지 마세요. 로그인 세션과 수집한 자료는 해당 OS 사용자 계정의 로컬 저장소에 보관됩니다.

## 설치 (0.3.11)

필요한 것: Python 3.12 이상, 인터넷 연결, KAIST 계정, 로컬 MCP stdio를 지원하는 클라이언트. Windows에서 설치·실행 검증했습니다. macOS/Linux 경로도 지원하지만 해당 OS의 실제 설치는 미검증입니다. Linux는 Chromium 실행에 필요한 시스템 라이브러리와 로그인 창을 표시할 데스크톱 환경이 필요합니다.

1. `kaist-study-0.3.11-source.zip`을 받아 개인 폴더에 압축을 풉니다. Git으로 받은 소스도 동일하게 설치할 수 있습니다.
2. 압축을 푼 폴더에서 터미널을 열고 실행합니다.

Windows PowerShell:

```powershell
py -3.12 install.py
```

macOS / Linux (Python 3.12 이상인지 확인):

```sh
python3 install.py
```

설치 스크립트는 `.venv` 생성, 실제 패키지 및 브라우저 의존성 설치, Chromium 다운로드·기동 검사, 독립 임시 저장소에서 MCP stdio 진단을 실행합니다. 실패하면 종료 코드 1로 중단하고 오류를 표시합니다. 설치 중 인터넷 다운로드가 발생하므로 시간이 걸릴 수 있습니다.

별도 위치에 가상환경을 만들려면 `--venv 경로`를 사용하세요. `--skip-browser-install`은 브라우저 설치와 기동 검사를 생략하는 고급 옵션이며, 이 경우 로그인과 브라우저 도구는 별도 Chromium 설치 전까지 준비되지 않습니다. 가상환경이 있는 폴더를 이동하거나 삭제하지 마세요. 이동했다면 새 위치에서 다시 설치하세요. 설치는 비편집 패키지이므로 소스를 바꾼 후에도 설치 스크립트를 다시 실행해야 합니다.

## MCP 클라이언트 연결

두 방법 중 하나만 사용하세요. 중복 등록하면 같은 서버가 두 번 실행됩니다.

### 설정 JSON으로 연결 (기본)

설치 완료 후 `.install/claude-desktop-config.json`에 생성된 **해당 PC용 설정 조각**을 MCP 클라이언트의 `mcpServers` 설정에 합칩니다. 기존 다른 서버 설정은 유지하세요. 설치 스크립트는 클라이언트 설정을 자동 수정하지 않습니다. 설정에 들어 있는 Python 실행파일 경로는 이 PC에서만 사용하세요.

서버는 설치된 패키지를 실행하므로 프로젝트 `PYTHONPATH`나 개발자 PC 경로가 필요하지 않습니다. 클라이언트를 완전히 종료한 뒤 다시 실행하세요. Linux에서는 로컬 MCP stdio를 지원하는 클라이언트를 사용하세요.

### Claude Desktop 확장으로 연결 (선택)

위 설치를 먼저 완료한 다음 `kaist-study-0.3.11.mcpb`를 설치합니다. 확장 설정의 **Installed Python executable**에는 설치 스크립트가 출력한 Python 경로를 넣습니다. 이 번들은 소스를 포함하지만 Python·의존성·Chromium은 별도로 설치해야 합니다. 파일 하나만으로 모든 런타임을 설치하는 오프라인 번들이 아닙니다.

확장 안의 진입점은 번들 기준 상대 경로를 사용합니다. 작성자의 PC 경로 또는 로그인 데이터가 포함되지 않습니다. MCPB를 업데이트할 때도 새 버전의 소스로 설치 스크립트를 다시 실행해 의존성을 맞추세요.

## 각자 로그인

클라이언트에서 `login_start`를 호출합니다. 열리는 전용 브라우저에서 자신의 KAIST SSO 로그인을 완료하고, 포탈의 KLMS와 Dooray 링크를 각각 열어 서비스 로그인을 확인합니다. `login_status(job_id)`로 상태를 확인하고 `login_finish(job_id)`로 완료합니다. 로그인 정보는 다른 사용자에게 전달하지 마세요.

같은 OS 사용자 계정에서는 기본적으로 같은 세션·저장소를 사용합니다. 여러 사람이 한 PC를 쓴다면 각자 OS 계정을 사용하세요. 고급 사용자는 MCP 설정의 `env`에서 `KAIST_DATA_DIR`와 `KAIST_BROWSER_PROFILE`을 각자 별도의 절대 경로로 설정할 수 있습니다. `.env`는 자동 로드하지 않으며 `.env.example`은 환경변수 참고용입니다. 일상적으로 쓰는 Chrome 프로필을 지정하지 마세요.

기본 저장소는 `platformdirs`로 결정됩니다. Windows는 `%LOCALAPPDATA%/KAIST/kaist-study`, macOS는 사용자 Application Support, Linux는 사용자 데이터 디렉터리를 사용합니다. 프로필의 `.mcp-session.json`에는 인증 쿠키가 있으므로 공유·커밋·첨부하지 마세요. 다운로드·메일 본문·데이터베이스·로그도 배포하지 마세요. 같은 프로필의 조회는 잠금으로 직렬화되므로 별도 CLI 로그인 창과 조회를 동시에 실행하지 마세요.

## 제공 기능과 제한

- KLMS: 현재 학기 강의, 공지와 페이지 순회, 진행 중 과제, 전체 주차의 자료·폴더·페이지·포럼, 첨부 다운로드, PDF/PPTX 추출, 중복 방지·변경 버전 보관.
- 포탈: 공개 홈페이지 공지와 로그인한 포탈의 공지사항·학사·연구·행정·수강/학적/논문·장학/복지·리더십/인턴/상담·교수학습 게시판 목록, 제목·내용·작성자 검색, 게시글 본문·이미지·첨부 링크.
- Dooray: 받은편지함 최신순 목록, 서버 검색·페이지 이동, 읽음 상태를 보존하는 원문 MIME 상세 조회.
- 개인 학사: 성적·강의·졸업만 허용합니다. 검증된 정확한 화면 URL을 설정하기 전까지 차단합니다. 다른 학사 메뉴와 미검증 데이터 API에는 접근하지 않습니다. 현재 개인 학사 화면 및 전체 강의 수·학점 합계는 미검증입니다.

`assignments_list`는 마감 전 미제출 과제가 기본이고 `active_only=false`로 과거 과제도 조회합니다. 메일 페이지는 0, 포탈 게시판 페이지는 1부터 시작합니다. 포탈 검색 `field`는 `title`, `content`, `title_content`, `author`입니다. `pagination.next_page`로 다음 페이지를 확인합니다.

로그인 필요·권한 부족·부분 실패·다운로드 실패·추출 실패를 결과에 구분합니다. 전체 자료 조회는 실제 계정에서 약 98초 걸렸으므로 특정 강의만 조회하거나 클라이언트의 도구 제한 시간을 충분히 설정하세요. 사이트 개편·사용자 권한 차이에 따라 추가 검증이 필요할 수 있습니다.

제출·수정·삭제·읽음 표시·출석·수강 신청·취소·신청서 제출·납부·개인정보 수정 기능은 제공하지 않습니다. HTTP 서버는 기본 비활성화 상태이며 공유 설치에도 stdio를 사용합니다.

## 개발 및 배포 검증

개발 환경에서는 설치된 가상환경의 Python으로 실행합니다. 아래는 Windows 예이며 macOS/Linux는 `.venv/bin/python`을 사용합니다.

```powershell
.venv/Scripts/python.exe -m pip install ".[browser,dev]"
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m compileall -q src scripts tests install.py
.venv/Scripts/python.exe scripts/diagnose_stdio.py
.venv/Scripts/python.exe scripts/build_mcpb.py
```

`dist/`에 소스 ZIP, MCPB, `SHA256SUMS.txt`가 생성됩니다. 빌더는 허용된 소스 파일만 수집하고 버전 일치·ZIP 무결성을 검사하며 로그인 프로필·계정 데이터·가상환경·개인 설정을 포함하지 않습니다. 이전 개인용 0.3.10 번들을 배포하지 마세요. 공유는 생성된 새 파일만 사용하세요. GitHub 업로드는 자동으로 수행하지 않습니다.

GitHub에 소스를 올리면 `.github/workflows/verify.yml`이 Windows·macOS·Ubuntu에서 테스트, Chromium 설치, 새 가상환경 설치 및 stdio 진단을 수행합니다. 워크플로 파일을 제공하는 것이며, 실제 GitHub 실행 결과는 저장소에 업로드한 뒤 확인해야 합니다.

원본 HTML·메일·실제 계정 데이터를 테스트 fixture로 넣지 마세요. 테스트는 합성 데이터와 모의 MCP 연결을 사용합니다. 설치 검사는 실제 설치된 패키지를 소스 경로 주입 없이 실행하며, 임시 저장소에서 수행되어 기존 로그인 세션에 접근하지 않습니다.
