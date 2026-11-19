# Colab Pro+에서 Qwen3.5-27B로 평가하기

팀 계정으로 **Colab Pro+**를 쓸 수 있어요. 답변 생성(④) 모델로 **Qwen3.5-27B**를 vLLM으로 띄워 골든셋 평가를 돌리는 방법이에요. 노트북은 이 가이드를 보고 따로 만들면 돼요.

## 1. 런타임과 모델 고르기

Colab 메뉴 **런타임 → 런타임 유형 변경**에서 GPU를 고르고, GPU 메모리에 맞는 모델을 써요.

| GPU (Colab Pro+) | 메모리 | 모델 | 비고 |
| --- | --- | --- | --- |
| H100 / A100 80GB | 80GB | `Qwen/Qwen3.5-27B` | 원본(bf16, 약 54GB) |
| A100 40GB | 40GB | `Qwen/Qwen3.5-27B-FP8` | FP8 양자화(약 28GB) |
| L4 | 24GB | `Qwen/Qwen3.5-27B-GPTQ-Int4` | 4비트 양자화(약 15GB). 문맥 길이를 8192 정도로 줄이기 |

- 모델 이름은 Hugging Face의 Qwen 공식 저장소 기준이에요 (2026-11-18 확인).
- 비교용 작은 모델은 `Qwen/Qwen3.5-9B`예요. 노션 평가 계획대로 9B와 27B를 같은 골든셋으로 비교하면 돼요.
- 처음 실행할 때 모델 다운로드에 시간이 걸려요 (27B 원본 기준 수십 분). Colab 세션이 끊기면 다시 받아야 해요.

## 2. 구성

```
Colab (/content/hr_chatbot)
├─ backend/      git clone (챗봇 코드: 답변 생성 프롬프트·파이프라인)
├─ DB/           git clone (규정·법령 조문, HR 데이터)
├─ RAG/   git clone (골든셋·평가 스크립트)
└─ vLLM 서버      http://127.0.0.1:8000/v1  (OpenAI 형식)
```

backend는 `LLM_BASE_URL`이 있으면 그 주소의 OpenAI 형식 서버로 답변을 생성해요. 그래서 vLLM을 띄우고 환경변수만 넣으면 돼요.

## 3. 순서

### 3-1. 받기·설치

```bash
mkdir -p /content/hr_chatbot && cd /content/hr_chatbot
git clone https://github.com/Jaeyong-s-HR-Chatbot/backend.git
git clone https://github.com/Jaeyong-s-HR-Chatbot/DB.git
git clone https://github.com/Jaeyong-s-HR-Chatbot/RAG.git
pip install -r backend/requirements.txt
pip install -U vllm
```

### 3-2. vLLM 띄우기

셀이 막히지 않게 백그라운드로 띄우고, `/v1/models`가 응답할 때까지 기다려요.

```bash
nohup vllm serve Qwen/Qwen3.5-27B-FP8 \
  --served-model-name qwen3.5-27b \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.90 \
  --port 8000 > /content/vllm.log 2>&1 &

curl http://127.0.0.1:8000/v1/models     # 준비되면 qwen3.5-27b가 보임. 안 되면 tail /content/vllm.log
```

- 모델은 1번 표에서 GPU에 맞는 것으로 바꿔요.
- `--max-num-seqs 16`은 꼭 넣어요. Qwen3.5는 Mamba 층이 있어서 기본값(256)이면 A100 40GB에서 `max_num_seqs (256) exceeds available Mamba cache blocks` 오류로 시작이 안 돼요. 평가는 한 번에 한 문항씩이라 16이면 충분해요.
- 생각 모드 끄기: `vllm serve --help`에 `--default-chat-template-kwargs`가 있으면 `--default-chat-template-kwargs '{"enable_thinking": false}'`를 붙여요 (아래 5번 참고).

### 3-3. 환경변수

| 환경변수 | 값 |
| --- | --- |
| `LLM_BASE_URL` | `http://127.0.0.1:8000/v1` |
| `LLM_MODEL` | vLLM의 `--served-model-name` (예: `qwen3.5-27b`) |
| `LLM_API_KEY` | `EMPTY` (vLLM을 키 없이 띄울 때) |
| `LLM_TIMEOUT` | `300` |
| `LLM_MAX_TOKENS` | `4096` (아래 5번 '생각 모드' 참고) |
| `SEARCH_MODE` | `keyword` (Colab에는 PostgreSQL이 없어서 조문 파일로 검색) |

### 3-4. 평가

```bash
cd /content/hr_chatbot/RAG
python scripts/eval_golden.py retrieval --split test                          # ② 검색 지표
python scripts/eval_golden.py generate --mode oracle --split dev --limit 20   # ④ 답변 생성 (프롬프트 조정은 dev로)
python scripts/eval_golden.py score --run eval/golden/runs/<방금 생긴 폴더>      # 채점
```

- 결과 폴더 이름에 모델명이 들어가요 (예: `..._oracle_qwen3.5-27b`).
- Opus 의미 판정은 `pip install anthropic` 후 `ANTHROPIC_API_KEY`를 Colab '보안 비밀'에 넣고 `score ... --judge` (비용 발생, 최종 test 때 한 번만 권장).
- 끝나면 `eval/golden/runs/`를 Google Drive에 복사해 두세요.

## 4. 무엇을 돌릴 수 있나

Colab에는 PostgreSQL(HR DB)이 없어요. 그래서 이렇게 나눠서 써요.

| 평가 | Colab에서 | 비고 |
| --- | --- | --- |
| ② 검색 지표 (`retrieval`) | 가능 | keyword 검색. BGE-M3 하이브리드는 DB(pgvector)가 있어야 함 |
| ④ 답변 생성 **oracle** (`generate --mode oracle`) | **가능 (추천)** | 정답 조문 + 비슷한 오답 조문 5개를 넣고 모델만 비교. 노션 '평가' 계획과 같은 방식 |
| 채점 (`score`) | 가능 | 규칙 채점. `--judge`는 Anthropic 키가 있을 때만 |
| ④ **e2e** (`generate --mode e2e`) | 본인 데이터 문항 제외하고 가능 | 본인 데이터 33문항은 HR DB가 있어야 함 → 로컬 PC에서 (아래 6번) |

## 5. 주의할 점

- **생각 모드**: Qwen3.5는 답하기 전에 `<think>…</think>`로 생각을 먼저 써요. backend가 이 부분을 지우긴 하지만, `LLM_MAX_TOKENS`(기본 600)를 생각이 다 써버리면 답이 잘려요. 그래서 4096으로 올려요. 응답 시간 비교(노션 기준 10초 이내)는 생각 모드를 끈 상태로 재요.
- **세션 시간**: Pro+도 오래 쓰면 끊겨요. 결과는 바로바로 Drive에 저장하세요.
- **같은 조건**: 9B·27B 비교 때는 같은 `--split`, 같은 `--limit`, 같은 backend 커밋(같은 프롬프트)으로 돌려야 공정해요.
- **test 세트는 한 번만**: 프롬프트를 고칠 때는 dev로만, test는 최종 비교 때 한 번만 돌려요.

## 6. 로컬 백엔드에 Colab 모델 붙이기 (선택)

로컬 PC에서 DB까지 띄운 상태로 27B를 쓰고 싶으면, Colab의 vLLM을 터널로 열고 로컬 `.env`의 LLM 주소만 바꿔요.

1. vLLM을 반드시 `--api-key <긴 임의 문자열>`을 붙여 띄워요 (주소를 아는 사람은 누구나 모델을 쓸 수 있어서).
2. Colab에서 cloudflared로 터널을 열면 `https://xxxx.trycloudflare.com` 주소가 나와요.
   ```bash
   wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O cloudflared && chmod +x cloudflared
   ./cloudflared tunnel --url http://127.0.0.1:8000
   ```
3. 로컬 `backend/.env`:
   ```
   LLM_BASE_URL=https://xxxx.trycloudflare.com/v1
   LLM_MODEL=qwen3.5-27b
   LLM_API_KEY=<vLLM에 넣은 키>
   LLM_TIMEOUT=300
   LLM_MAX_TOKENS=4096
   ```
4. 키와 터널 주소는 깃·노션에 올리지 말고, 다 쓰면 Colab 세션을 종료해요.
