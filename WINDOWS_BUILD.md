# Blog Helper macOS/Windows 자동 빌드

`main` 브랜치에 기능을 커밋하면 GitHub Actions가 같은 `main.py`로 Windows의 `BlogHelper.exe`와 macOS의 `BlogHelper.app`을 동시에 생성합니다.

## 처음 한 번

1. GitHub에서 새 저장소를 만듭니다.
2. 이 폴더 전체를 저장소의 `main` 브랜치에 올립니다.
3. GitHub 저장소의 `Actions` 탭에서 `Build macOS and Windows Apps`를 선택합니다.
4. `Run workflow`를 누릅니다.

빌드가 끝나면 실행 화면 아래 `Artifacts`에서 두 파일을 내려받습니다.

- `BlogHelper-Windows`: 바로 실행할 수 있는 `BlogHelper.exe`와 ZIP
- `BlogHelper-macOS`: `BlogHelper.app`이 들어 있는 ZIP

## 새 버전 배포

`v1.0.0`처럼 `v`로 시작하는 태그를 푸시하면 GitHub Release에도 Windows EXE/ZIP과 macOS ZIP이 자동 첨부됩니다.

```bash
git tag v1.0.0
git push origin v1.0.0
```

## Windows 실행 조건

- Windows 10 또는 Windows 11 64비트
- Google Chrome 설치 필요
- Python 설치 불필요
- Playwright 코드는 EXE에 포함됨
- 로그인 세션과 설정은 `%LOCALAPPDATA%\Blog Helper`에 저장됨

## macOS 실행 조건

- macOS 11 이상
- Google Chrome 설치 필요
- Python 설치 불필요
- 자동 빌드 파일은 Intel Mac용이며 Apple Silicon에서는 Rosetta로 실행
- 최초 실행 차단 시 앱을 Control-클릭하고 `열기` 선택
