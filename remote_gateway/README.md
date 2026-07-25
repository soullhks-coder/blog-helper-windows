# Blog Helper Remote

휴대폰이나 노트북에서 키워드를 입력하고, 온라인 상태인 Blog Helper PC를 선택해 로컬 자동화 대기열에 작업을 추가하는 Cloudflare Worker입니다.

## 배포 준비

1. `lhksoul.com` 도메인을 Cloudflare DNS에서 관리합니다.
2. Cloudflare 계정에서 Workers 유료 플랜 또는 Durable Objects를 사용할 수 있는 플랜을 확인합니다.
3. 이 폴더에서 `npm install` 후 아래 비밀값을 등록합니다.

```bash
npx wrangler secret put CONTROL_PASSWORD
npx wrangler secret put SESSION_SECRET
npx wrangler secret put AGENT_TOKEN
npx wrangler deploy
```

- `CONTROL_PASSWORD`: 웹 로그인 비밀번호
- `SESSION_SECRET`: 32자 이상의 임의 문자열
- `AGENT_TOKEN`: PC 앱과 서버에 동일하게 입력할 32자 이상의 임의 문자열

`wrangler.jsonc`의 custom domain이 배포되면 Cloudflare가 `ai.lhksoul.com` DNS 레코드와 인증서를 자동 생성합니다. 수동 CNAME을 먼저 만들지 않습니다.

## GitHub Actions 자동 배포

GitHub 저장소 `Settings > Secrets and variables > Actions`에 아래 두 값을 등록합니다.

- `CLOUDFLARE_API_TOKEN`: Workers 편집과 해당 DNS Zone 편집 권한이 있는 API 토큰
- `CLOUDFLARE_ACCOUNT_ID`: Cloudflare 계정 ID

그 다음 GitHub Actions의 `Deploy Remote Web`을 실행하면 Worker 번들 검증 후 배포됩니다. 두 값이 없으면 검증까지만 하고 배포는 건너뜁니다.

Worker 비밀값은 최초 한 번 Cloudflare의 Worker 설정 또는 Wrangler에서 등록합니다.

```bash
npx wrangler secret put CONTROL_PASSWORD
npx wrangler secret put SESSION_SECRET
npx wrangler secret put AGENT_TOKEN
```

`AGENT_TOKEN`은 Blog Helper PC 설정에도 동일하게 입력하지만, GitHub 저장소나 웹 소스에는 넣지 않습니다.

## PC 연결

Blog Helper의 `환경설정 > 기본설정 > 원격 제어`에서 아래 값을 저장합니다.

- 서버 주소: `https://ai.lhksoul.com`
- PC 이름: 웹에서 구분할 이름
- 에이전트 토큰: 위 `AGENT_TOKEN`
- 원격 연결 사용: 켬

PC는 외부에서 들어오는 포트를 열지 않고 Cloudflare로 WebSocket을 연결합니다. 워드프레스, 티스토리, AI API 키와 프롬프트는 PC 밖으로 전송하지 않습니다.
