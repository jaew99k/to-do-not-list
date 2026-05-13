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

    # 1단계: 사용자 유형 분석
    profile_prompt = f"""You are a productivity expert. Respond with valid JSON only. No explanation. Start with {{ and end with }}.

사용자 성향 분석:
- 새로운 일을 시작할 때: {answers.get('q1', '없음')}
- 마감이 다가올 때: {answers.get('q2', '없음')}
- 결과물이 완성됐을 때: {answers.get('q3', '없음')}
- 예상보다 시간이 생겼을 때: {answers.get('q4', '없음')}
- 여러 일정이 겹쳤을 때: {answers.get('q5', '없음')}
- 일이 잘 안 풀릴 때: {answers.get('q6', '없음')}

사용자 유형 4가지:
- 완성추구형 (A가 많음): 결과물 완성도와 세부 품질을 중요시
- 체계정리형 (B가 많음): 전체 흐름과 우선순위를 먼저 정리
- 준비안정형 (C가 많음): 심리적으로 안정된 상태에서 업무 진입
- 맥락파악형 (D가 많음): 충분한 정보와 배경을 파악한 뒤 시작

아래 JSON 형식으로만 응답하세요:
{{
  "user_type": "유형명",
  "user_type_desc": "유형 설명 한 문장",
  "tendencies": [
    {{"label": "패턴 이름", "desc": "설명", "level": 1~5}}
  ],
  "weekly_score": 0~100,
  "weekly_headline": "이번 주 핵심 메시지 한 줄",
  "praise": [
    {{"title": "칭찬 제목", "body": "칭찬 내용", "impact": "긍정적 효과", "color": "#a78bfa"}}
  ],
  "still_working": [
    {{"label": "개선 중인 패턴", "progress": 0~100}}
  ]
}}"""

    profile_response = requests.post(
        "https://api.upstage.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {UPSTAGE_API_KEY}", "Content-Type": "application/json"},
        json={"model": "solar-pro3", "messages": [{"role": "user", "content": profile_prompt}], "temperature": 0.7, "max_tokens": 2000},
        timeout=60
    )
    profile_data = profile_response.json()
    profile_text = profile_data["choices"][0]["message"]["content"].strip()
    if profile_text.startswith("```"):
        profile_text = profile_text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        profile = json.loads(profile_text)
    except:
        profile = {"user_type": "분석형", "user_type_desc": "분석 중", "tendencies": [], "weekly_score": 70, "weekly_headline": "이번 주도 잘 해낼 수 있어요", "praise": [], "still_working": []}

    # 2단계: 날짜별 To-Do-Not 분석
    # 날짜 목록 추출
    date_set = set()
    for ev in events:
        start = (ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date") or "")[:10]
        end_raw = (ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date") or "")[:10]
        if start:
            date_set.add(start)
        # 여러 날짜에 걸친 일정 처리
        if end_raw and end_raw > start:
            from datetime import date as date_type, timedelta
            try:
                s = date_type.fromisoformat(start)
                e = date_type.fromisoformat(end_raw) - timedelta(days=1)
                while s <= e:
                    date_set.add(s.isoformat())
                    s += timedelta(days=1)
            except:
                pass

    dates = sorted(date_set)

    days = {}
    for d in dates:
        day_events = [ev.get("summary", "제목 없음") for ev in events
                      if (ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date") or "")[:10] <= d
                      and (ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date") or "")[:10] >= d]

        day_prompt = f"""You are a productivity expert. Respond with valid JSON only. No explanation. Start with {{ and end with }}.

사용자 유형: {profile.get('user_type', '')}
사용자 성향: {profile.get('user_type_desc', '')}
이번 주 전체 일정: {json.dumps([ev.get('summary', '') for ev in events], ensure_ascii=False)}
오늘 날짜: {d}
오늘 일정: {json.dumps(day_events, ensure_ascii=False)}

위 정보를 바탕으로 {d}의 To-Do-Not을 만들어주세요.
이번 주 전체 흐름을 고려해서 오늘 하지 말아야 할 행동 3개를 생성하세요.

아래 JSON 형식으로만 응답하세요:
{{
  "riskLevel": "high 또는 medium 또는 low",
  "focusGoal": "오늘의 핵심 목표 한 줄",
  "items": [
    {{
      "risk": "red 또는 orange 또는 purple",
      "action": "하지 말아야 할 구체적인 행동",
      "reason": "이유",
      "timeLimit": "시간 제한 조건",
      "instead": "대신 해야 할 것"
    }},
    {{
      "risk": "orange",
      "action": "두 번째 하지 말아야 할 행동",
      "reason": "이유",
      "timeLimit": "시간 제한",
      "instead": "대신 할 것"
    }},
    {{
      "risk": "purple",
      "action": "세 번째 하지 말아야 할 행동",
      "reason": "이유",
      "timeLimit": "시간 제한",
      "instead": "대신 할 것"
    }}
  ]
}}"""

        try:
            day_response = requests.post(
                "https://api.upstage.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {UPSTAGE_API_KEY}", "Content-Type": "application/json"},
                json={"model": "solar-pro3", "messages": [{"role": "user", "content": day_prompt}], "temperature": 0.7, "max_tokens": 1500},
                timeout=60
            )
            day_data = day_response.json()
            day_text = day_data["choices"][0]["message"]["content"].strip()
            if day_text.startswith("```"):
                day_text = day_text.split("\n", 1)[1].rsplit("```", 1)[0]
            days[d] = json.loads(day_text)
        except Exception as e:
            print(f"{d} 분석 실패: {e}")
            days[d] = {
                "riskLevel": "medium",
                "focusGoal": "오늘 할 일에 집중하기",
                "items": []
            }

    profile["days"] = days
    return {"result": profile}