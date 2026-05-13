from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()

# Render 환경에서는 JSON 내용을 환경변수에서 직접 읽음
GOOGLE_CLIENT_SECRET_JSON = os.getenv("GOOGLE_CLIENT_SECRET_JSON")
if GOOGLE_CLIENT_SECRET_JSON:
    with open("client_secret.json", "w") as f:
        f.write(GOOGLE_CLIENT_SECRET_JSON)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

UPSTAGE_API_KEY = os.getenv("UPSTAGE_API_KEY")
CLIENT_SECRET_FILE = os.getenv("GOOGLE_CLIENT_SECRET_FILE")
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
REDIRECT_URI = "https://to-do-not-list.onrender.com/auth/callback"

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

temp_store = {}

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/login")
async def login():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    auth_url, state = flow.authorization_url(
        prompt="consent",
        access_type="offline"
    )
    temp_store["state"] = state
    temp_store["code_verifier"] = flow.code_verifier
    return RedirectResponse(auth_url)

@app.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str):
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        state=state
    )
    flow.code_verifier = temp_store.get("code_verifier")
    flow.fetch_token(code=code)
    credentials = flow.credentials

    service = build("calendar", "v3", credentials=credentials)

    now = datetime.now(timezone.utc)
    one_week_later = now + timedelta(days=14)

    events_result = service.events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=one_week_later.isoformat(),
        maxResults=50,
        singleEvents=True,
        orderBy="startTime"
    ).execute()
    events = events_result.get("items", [])

    temp_store["events"] = events

    return RedirectResponse("/onboarding")

@app.get("/onboarding")
async def onboarding(request: Request):
    events = temp_store.get("events", [])
    return templates.TemplateResponse(
        request=request,
        name="onboarding.html",
        context={"events": json.dumps(events, ensure_ascii=False)}
    )

@app.post("/analyze")
async def analyze(request: Request):
    body = await request.json()
    events = body.get("events", [])
    answers = body.get("answers", {})

    prompt = f"""You are a productivity expert. Respond with valid JSON only. No explanation, no thinking out loud, no markdown. Start your response with {{ and end with }}.

당신은 사용자의 생산성을 방해하는 요소를 분석하는 전문가입니다.

아래는 사용자의 성향 분석 결과입니다:
- 새로운 일을 시작할 때: {answers.get('q1', '없음')}
- 마감이 다가올 때: {answers.get('q2', '없음')}
- 결과물이 완성됐을 때: {answers.get('q3', '없음')}
- 예상보다 시간이 생겼을 때: {answers.get('q4', '없음')}
- 여러 일정이 겹쳤을 때: {answers.get('q5', '없음')}
- 일이 잘 안 풀릴 때: {answers.get('q6', '없음')}

사용자 유형은 아래 4가지 중 답변 패턴에 따라 판단하세요:
- 완성추구형 (A가 많음): 결과물 완성도와 세부 품질을 중요시, 마지막까지 수정하는 성향
- 체계정리형 (B가 많음): 전체 흐름과 우선순위를 먼저 정리해야 안정적으로 움직이는 성향
- 준비안정형 (C가 많음): 심리적으로 안정된 상태에서 업무에 진입하려는 성향
- 맥락파악형 (D가 많음): 충분한 정보와 배경을 파악한 뒤 본격적으로 시작하는 성향

아래는 이번 주 전체 일정입니다 (날짜순):
{json.dumps(events, ensure_ascii=False)}

[분석 방법]
1. 위 일정 전체를 먼저 파악하세요. 마감이 언제인지, 어떤 날이 바쁜지, 일정들이 서로 어떻게 연결되는지 파악하세요.
2. 일정이 있는 날짜마다 To-Do-Not 항목을 생성하세요.
3. 각 날짜의 항목은 당일 일정만 고려하지 말고, 이번 주 전체 흐름을 고려하세요.
   - 예: 목요일에 발표가 있으면, 화요일 항목에 "발표 준비를 내일로 미루지 않기"가 포함될 수 있음
   - 예: 수요일에 마감이 있으면, 월요일부터 "마감 전날 처음부터 다시 쓰지 않기 위해 오늘 초안 완성하기"가 반영될 수 있음
4. 사용자의 성향(완벽주의, 계획중독 등)을 반드시 반영해서, 그 사람이 특히 빠지기 쉬운 함정을 짚어주세요.
5. 각 날짜마다 최소 3개 이상의 항목을 생성하세요.
6. riskLevel은 해당 날짜의 일정 밀도와 마감 압박을 고려해서 high/medium/low로 설정하세요.

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만 출력하세요. 절대로 분석 과정이나 설명을 먼저 쓰지 마세요. 첫 글자는 반드시 {{ 이어야 합니다:
{{
  "user_type": "사용자 유형 한마디 (예: 완벽주의 + 계획중독형)",
  "user_type_desc": "유형 설명 한 문장",
  "tendencies": [
    {{"label": "패턴 이름", "desc": "설명", "level": 1~5 숫자}}
  ],
  "days": {{
    "YYYY-MM-DD": {{
      "riskLevel": "high 또는 medium 또는 low",
      "focusGoal": "오늘의 핵심 목표 한 줄 (이번 주 전체 흐름 반영)",
      "items": [
        {{
          "risk": "red 또는 orange 또는 purple",
          "action": "하지 말아야 할 구체적인 행동",
          "reason": "이유 (이번 주 전체 맥락과 연결해서 설명)",
          "timeLimit": "시간 제한 조건",
          "instead": "대신 해야 할 것"
        }}
      ]
    }}
  }},
  "weekly_score": 0~100 사이 숫자 (이번 주 일정 난이도와 사용자 성향 기반 예상 실행 점수),
  "weekly_headline": "이번 주 핵심 메시지 한 줄 (예: 구조 먼저, 표현은 나중에)",
  "praise": [
    {{
      "title": "칭찬 제목 (예: 자료조사 시간을 제한할 수 있어요)",
      "body": "구체적인 칭찬 내용 (이번 주 일정 기반으로 잘 해낼 수 있는 부분)",
      "impact": "이렇게 하면 생기는 긍정적 효과",
      "color": "#a78bfa 또는 #34d399 또는 #fb923c 중 하나"
    }}
  ],
  "still_working": [
    {{"label": "아직 개선 중인 패턴 이름", "progress": 0~100 숫자}}
  ]
}}"""

    try:
        response = requests.post(
            "https://api.upstage.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {UPSTAGE_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "solar-pro3",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 8000
            },
            timeout=120
        )

        print("API 응답 상태:", response.status_code)
        result = response.json()
        print("API 응답 내용:", json.dumps(result, ensure_ascii=False)[:500])

        answer = result["choices"][0]["message"]["content"]

        # JSON 파싱 시도
        clean = answer.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            clean = clean.rsplit("```", 1)[0]
        parsed = json.loads(clean)
        return {"result": parsed}
    except json.JSONDecodeError:
        print("JSON 파싱 실패, 원본 텍스트 반환")
        return {"result": answer}
    except Exception as e:
        print(f"API 호출 에러: {e}")
        return {"result": f"에러가 발생했습니다: {str(e)}"}