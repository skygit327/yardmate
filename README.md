# YardMate

동남권(부산·울산·경남) 조선소 협력사를 위한 검사·지원사업 에이전트입니다. 동남권 이노베이션 해커톤 주제 2 출품작이며, Spec Agent에서 파생했습니다.

- **검사기준 양식 읽기**: 한국어 조선소 양식, 영어 도장 사양, 중국어 선주 양식을 같은 기준표로 정리합니다.
- **로트 판정**: 용접·도장 측정값을 해당 고객 기준으로 판정하고, 판정 근거로 고객 원문을 함께 보여 줍니다.
- **3개 언어 성적서**: 한 번 승인하면 영어·한국어·중국어 PDF·Excel 성적서가 나옵니다.
- **조선업 위기지역 지원사업**: 거제·통영·고성·울산 동구·창원 진해구 조건을 회사 프로필로 판정하고, 제출서류와 마감을 할 일로 등록합니다.

시연 데이터(회사, 조선소, 선주, 공고)는 모두 가상이며 실제 기관의 기준이 아닙니다.

## 실행

Python 3.12 이상, `uv`, Node.js 22.12 이상이 필요합니다. 서버가 `fcntl`을 쓰므로 macOS/Linux에서 실행하고, Windows에서는 WSL에서 실행합니다.

```bash
uv sync
npm --prefix frontend ci
npm --prefix frontend run build
uv run spec-agent create-user --username demo --name "(가상) 한빛조선기자재 품질팀"
uv run spec-agent serve --port 8770
```

1. `http://127.0.0.1:8770`을 열어 로그인합니다.
2. 홈의 **'조선 시연 데이터 불러오기'**를 누르면 양식 3종, 로트 4건, 공고 3건이 들어옵니다.

## 테스트

```bash
uv run pytest -q
npm --prefix frontend test
```
