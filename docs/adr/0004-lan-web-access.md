# ADR 0004: 같은 Wi-Fi의 웹 접속과 API 중계

날짜: 2026-09-09\
상태: 채택

## 접속 구조

사용자 요청에 따라 같은 Wi-Fi/LAN의 기기가 Mac의 내부 IPv4와 `3000` 포트로 웹 화면에 접속하도록 한다. 웹 개발 서버는 `0.0.0.0:3000`을 사용하고, 실행 스크립트는 활성 인터페이스의 사설 IPv4를 시작할 때마다 출력한다. 포트 충돌은 오류로 처리해 접속 주소가 임의로 달라지지 않게 한다.

브라우저는 `/api/status`, `/api/chat`을 같은 출처로 요청한다. `app/api/[...path]/route.ts`가 서버 전용 `API_BASE_URL`의 FastAPI로 전달하므로 휴대폰의 `localhost`를 Mac의 API로 오인하지 않는다. 개발 서버와 빌드된 요청 처리기에서 같은 route를 사용한다.

FastAPI `127.0.0.1:8000`, 모델 `127.0.0.1:8080`, PostgreSQL `127.0.0.1:5432`는 유지한다. 기존 브라우저용 `NEXT_PUBLIC_API_BASE_URL`은 제거한다. LAN IP 접속에는 Vite 기본 허용 정책을 사용하며 `allowedHosts=true`나 CORS 전체 허용을 추가하지 않는다. [Vite 서버 설정](https://vite.dev/config/server-options)

## 중계 계약

현재 허용하는 경로와 메서드는 `GET /api/status`, `POST /api/chat`뿐이다. 대상은 사용자명·비밀번호·경로·쿼리가 없는 HTTP loopback `127.0.0.1` 주소여야 한다. 브라우저 쿼리는 허용된 API에만 전달하며 redirect는 따라가지 않는다. 다른 API는 `404`, 맞지 않는 메서드는 `405`, 내부 연결 실패는 세부 오류 없이 `502`로 응답한다.

요청의 Accept·Content-Type과 응답의 Content-Type·Cache-Control·Retry-After·X-Accel-Buffering만 전달한다. 현재 로그인 기능이 없으므로 쿠키·인증 헤더는 전달하지 않는다. 인증 구현에서는 로그인 경로와 Cookie·Set-Cookie·Origin·CSRF 전달 규칙을 함께 확장해야 한다.

요청 본문과 SSE 응답은 읽어 모으지 않고 스트림으로 전달한다. Request의 취소 신호를 upstream fetch에 연결하며 응답 스트림 취소도 전파한다. 설치된 vinext의 production Node 어댑터는 응답 헤더 이전의 요청 취소 신호를 연결하지 않는 제한이 있다. 현재 FastAPI는 첫 SSE meta 이벤트를 즉시 반환하며 헤더 이후 스트림 취소는 전달된다. worker 중지·자원 회수의 종단 검증은 생성 작업 단계에서 계속 다룬다.

## 검증과 적용 범위

프록시 회귀 테스트 6개, 내부 IP 선택 테스트 2개, lint와 프런트 빌드를 검증했다. 실행 중인 Mac의 LAN IP로 화면·API·입력 오류·실제 모델 SSE 완료를 확인했고, 빌드된 production 처리기의 화면과 API도 별도 서버 없이 직접 호출해 확인했다.

같은 Wi-Fi 접속은 인터넷 배포가 아니다. 현재는 로그인 연결 전 로컬 MVP이며, 공유기의 기기 격리와 macOS 수신 연결 설정은 실제 다른 기기의 접속에 영향을 줄 수 있다. 이 변경은 외부 포트 포워딩이나 호스팅 설정을 추가하지 않는다.
