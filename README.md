# 포켓몬스토어 재고 감시

포켓몬스토어 상품 페이지를 주기적으로 확인하고, `품절` 또는 `SOLD OUT` 상태에서 `구매하기` / `장바구니`가 보이는 구매 가능 상태로 바뀌면 ntfy.sh 푸시 알림을 보냅니다.

## 파일 구조

```text
monitor.py
products.json
requirements.txt
.github/workflows/stock-monitor.yml
README.md
```

실행 후에는 이전 상태 저장을 위해 `.stock_state.json` 파일이 자동 생성됩니다. GitHub Actions 워크플로가 이 파일을 저장소에 커밋해서 다음 실행 때 상태 변화를 비교합니다.

## 상품 URL 관리

`products.json`에 감시할 상품 URL을 추가하세요.

```json
[
  "https://www.pokemonstore.co.kr/pages/product/product-detail.html?productNo=114169373"
]
```

객체 형식도 사용할 수 있습니다.

```json
[
  {
    "url": "https://www.pokemonstore.co.kr/pages/product/product-detail.html?productNo=114169373"
  }
]
```

`products.json` 대신 `products.txt`를 사용할 수도 있습니다. 한 줄에 URL 하나씩 적으면 됩니다.

## ntfy 설정

1. ntfy 앱을 휴대폰에 설치합니다.
2. 임의의 토픽을 정합니다. 예: `https://ntfy.sh/my-private-pokemon-topic`
3. GitHub 저장소에서 `Settings` → `Secrets and variables` → `Actions` → `New repository secret`을 엽니다.
4. 이름은 `NTFY_TOPIC_URL`, 값은 ntfy 토픽 전체 URL로 저장합니다.

## GitHub Actions 실행

`.github/workflows/stock-monitor.yml`은 10분마다 실행되도록 설정되어 있습니다.

```yaml
schedule:
  - cron: "*/10 * * * *"
```

5분마다 실행하고 싶다면 아래처럼 바꾸면 됩니다.

```yaml
schedule:
  - cron: "*/5 * * * *"
```

GitHub Actions 예약 실행은 GitHub 상황에 따라 몇 분 지연될 수 있습니다.

## 로컬 테스트

```bash
pip install -r requirements.txt
set NTFY_TOPIC_URL=https://ntfy.sh/your-topic
python monitor.py
```

PowerShell에서는 아래처럼 환경 변수를 설정합니다.

```powershell
$env:NTFY_TOPIC_URL = "https://ntfy.sh/your-topic"
python monitor.py
```

## 재고 판단 기준

HTML 텍스트에서 아래 문구를 기준으로 판단합니다.

- 품절 판단: `SOLD OUT`, `품절`, `일시 품절`, `재고 없음`, `판매 종료`, `구매 불가`
- 구매 가능 판단: `구매하기`, `바로 구매`, `장바구니`, `cart`, `buy now`

`품절 -> 재고있음`으로 바뀔 때만 알림을 보냅니다. 첫 실행부터 재고가 있는 상품도 알림을 받고 싶다면 GitHub Actions 환경 변수에 `ALERT_ON_FIRST_IN_STOCK: "true"`를 추가하세요.
