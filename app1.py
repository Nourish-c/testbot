import streamlit as st
import openai
import gspread
from datetime import datetime
import random
import string
import logging

MAX_PER_CONDITION = 18
TOTAL_LIMIT = 72

SPREADSHEET_IDS = {
    ("술어 미러링", "반말"): "1QYmaCENdagP2Ao5ac_M-9l1tzpcjk00tnfR9EXseR24",
    ("술어 미러링", "존댓말"): "1n1G_xILG2WBkdDlDm1h7eV8vSCvp0J2a7DEKRKTj-4o",
    ("없음", "반말"): "1hM0q2AJqJ74xEUWC5KXBPE0cYzEDWbUj3jBYYo6ZR60",
    ("없음", "존댓말"): "1VGVkanFcGxABZPLnSKobHsop7kiTFsbKxW7GM3Rl8CY",
}

ALLOCATION_SHEET_ID = "1RpVn5Px_fJ_dsFOuw3Rv05FHM8CIAGdfaC_twEBrH5s"

client = openai.OpenAI(api_key=st.secrets["openai"]["api_key"])
gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"])

def extract_keywords_via_gpt(user_input):
    system_prompt = (
        "다음 문장에서 중요한 핵심 단어(명사, 형용사, 동사 위주) 2~3개만 뽑아줘. "
        "쉼표로 구분하고, 다른 설명은 하지 마."
    )
    try:
        response = client.chat.completions.create(
            model="gpt-4-1106-preview",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ],
            temperature=0,
            max_tokens=30
        )
        keywords = response.choices[0].message.content.strip()
        return [k.strip() for k in keywords.split(",") if k.strip()]
    except Exception as e:
        logging.error(f"[GPT keyword extraction error] {e}")
        return []

def is_consistent_tone(response, is_formal):
    formal_endings = ("군요", "네요", "셨어요")
    informal_endings = ("구나", "네", "았어", "었어")
    if is_formal:
        return all(not response.endswith(end) for end in informal_endings)
    else:
        return all(not response.endswith(end) for end in formal_endings)

def generate_mirroring_sentence_only(user_input, is_formal, keywords, question):
    role_prompt = (
        f"당신은 사용자와 대화를 이어가는 친절한 친구{' (반말 사용)' if not is_formal else ' (존댓말 사용)'} 또는 정중한 상담자{' (존댓말 사용)' if is_formal else ' (반말 사용)'}입니다.\n"
        "사용자가 한 말에 담긴 핵심 단어를 포함하여 자연스럽게 문장을 변형하여 응답하고, 사용자의 감정에 공감하는 어미를 사용해야 합니다.\n"
        "단, 다음 조건을 반드시 지켜야 합니다:\n"
        f"- 반드시 {'존댓말' if is_formal else '반말'}만 사용하고, {'반말' if is_formal else '존댓말'}은 절대 사용하지 마세요.\n"
        f"- 사용할 수 있는 어미는 다음과 같습니다:\n"
        f"{'반말: ~구나, ~았구나 / ~었구나, ~하네 / ~했네, ~였어? / ~한 거야?' if not is_formal else '존댓말: ~군요 / ~네요, ~셨군요 / ~하셨네요, ~하시네요 / ~하셨네요, ~하신 건가요? / ~였나요?'}\n"
        "- 응답은 핵심 의미를 자연스럽게 변형한 1문장으로 작성하고, 감정 공감을 표현하는 어미로 끝나야 합니다.\n"
        "- 절대로 질문하지 마세요.\n"
        f"- 문장에는 다음 키워드 중 하나 이상을 반드시 포함해야 합니다: {', '.join(keywords)}"
    )
    try:
        response = client.chat.completions.create(
            model="gpt-4-1106-preview",
            messages=[
                {"role": "system", "content": role_prompt},
                {"role": "user", "content": user_input}
            ],
            temperature=0.5,
            max_tokens=80,
        )
        mirrored = response.choices[0].message.content.strip()

        connector = " 그렇다면 " if is_formal else " 그러면 "

        # (O) 또는 (X) 텍스트 제거 (잠시 넘어가기로 했으므로 이 부분은 필요에 따라 다시 추가)
        # mirrored = mirrored.replace(" (O)", "").replace(" (X)", "")

        return f"{mirrored}{connector}{question}"
    except Exception as e:
        logging.error(f"[GPT Error] {e}")
        fallback_base = "그렇군요" if is_formal else "그렇구나"
        connector = " 그렇다면 " if is_formal else " 그러면 "
        return f"{fallback_base}.{connector}{question}"

def update_count_batch(spreadsheet_id, worksheet_name, tone, mirror):
    sh = gc.open_by_key(spreadsheet_id)
    ws = sh.worksheet(worksheet_name)
    all_values = ws.get_all_values()
    headers = all_values[0]
    tone_idx = headers.index("Tone")
    mirror_idx = headers.index("Mirroring")
    count_idx = headers.index("Count")
    target_row = None
    for i, row in enumerate(all_values[1:], start=2):
        if row[tone_idx].strip() == tone and row[mirror_idx].strip() == mirror:
            target_row = i
            current_count = int(row[count_idx])
            break
    if target_row is None:
        raise ValueError("조건에 맞는 행을 찾을 수 없습니다.")
    sheet_id = ws._properties["sheetId"]
    requests = [{
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": target_row - 1,
                "endRowIndex": target_row,
                "startColumnIndex": count_idx,
                "endColumnIndex": count_idx + 1,
            },
            "rows": [{
                "values": [{
                    "userEnteredValue": {"numberValue": current_count + 1}
                }]
            }],
            "fields": "userEnteredValue"
        }
    }]
    sh.batch_update({"requests": requests})

ws_allocation = gc.open_by_key(ALLOCATION_SHEET_ID).worksheet("할당현황")

def allocate_condition():
    all_values = ws_allocation.get_all_values()
    header = all_values[0]
    data = [dict(zip(header, row)) for row in all_values[1:]]
    total_count = sum(int(row["Count"]) for row in data)
    if total_count >= TOTAL_LIMIT:
        return None, None, True, None
    eligible = [row for row in data if int(row["Count"]) < MAX_PER_CONDITION]
    if not eligible:
        return None, None, True, None
    choice = random.choice(eligible)
    tone = choice["Tone"].strip()
    mirror = choice["Mirroring"].strip()
    first_letter = {("반말", "없음"): "A", ("반말", "술어 미러링"): "B", ("존댓말", "없음"): "C", ("존댓말", "술어 미러링"): "D"}.get((tone, mirror), "")
    update_count_batch(ALLOCATION_SHEET_ID, "할당현황", tone, mirror)
    return tone, mirror, False, first_letter

def get_questions(tone):
    if tone == "반말":
        return [
            "최근에 본 영화 중에 제일 기억에 남는 장면이 어떤 거야?",
            "영화를 다른 사람에게 추천한다면 어떤 점을 강조하고 싶어?",
            "영화 볼 때 팝콘 말고 즐겨 먹는 간식이 있어? 있다면 어떤 거야?",
            "드라마에 나오는 악당 중에 제일 인상 깊었던 사람은 누구야?",
            "코미디 영화랑 액션 영화 중에 어떤 거 더 좋아해?",
            "영화관에서 어디 자리에 앉는 거 제일 좋아해?",
            "집에서 혼자 영화 보는 거랑 다른 사람들이랑 같이 보는 거 중에 뭐가 더 좋아?",
            "영화 볼 때 제일 집중 안 됐던 적이 언제야? (예: 광고 할 때, 옆 사람 시끄러울 때)",
            "가장 기억에 남는 영화 속 캐릭터의 특징이 뭐야?",
            "영화나 드라마 결말이 완전 예상 밖으로 가면 기분 어때?",
            "만약에 영화 주인공이 될 수 있다면 어떤 영화 주인공을 해보고 싶어?",
            "영화 보다가 너무 현실이랑 다르다고 느낀 내용은 어떤 내용이야?",
            "영화 주인공처럼 특별한 능력 하나 가질 수 있다면 어떤 능력 갖고 싶어?",
            "드라마를 통해 새로운 사실이나 정보를 알게 된 적 있어? 있다면 어떤 내용이야?",
            "영화 시작 전에 광고 너무 길게 하는 거 어떻게 생각해?",
        ]
    else:
        return [
            "최근에 감상하신 영화 중에서 가장 인상 깊었던 장면은 무엇이었나요?",
            "가장 기억에 남는 영화 속 캐릭터의 특징은 무엇이었나요?",
            "영화 관람 시 팝콘 외에 즐겨 드시는 간식이 있으신가요? 있다면 무엇인가요?",
            "드라마에 등장하는 악역 중에서 가장 인상 깊었던 인물은 누구인가요?",
            "코미디 영화와 액션 영화 중 어떤 장르를 더 선호하시나요?",
            "영화관에서 가장 선호하시는 좌석 위치는 어디인가요?",
            "집에서 혼자 영화를 시청하는 것과 다른 사람들과 함께 보는 것 중 어느 쪽을 더 좋아하시나요?",
            "영화를 보실 때 가장 집중하기 어려웠던 순간은 언제였나요? (예: 광고 상영 시, 주변 관람객의 소음)",
            "영화를 다른 사람에게 추천하신다면 어떤 점을 강조하고 싶으신가요?",
            "영화나 드라마의 결말이 예상치 못한 방향으로 흘러간다면 어떤 느낌이 드시나요?",
            "만약 영화 속 주인공이 될 수 있다면 어떤 영화의 주인공 역할을 해보고 싶으신가요?",
            "영화를 보시다가 비현실적이라고 느껴진 내용은 어떤 내용이었나요?",
            "영화 주인공과 같은 특별한 능력을 하나 가질 수 있다면 어떤 능력을 갖고 싶으신가요?",
            "드라마를 통해 새로운 사실이나 정보를 알게 되신 적이 있으신가요? 있다면 어떤 내용이었나요?",
            "영화 시작 전 광고가 너무 길게 상영되는 것에 대해 어떻게 생각하시나요?",
        ]

def log_to_sheet(pid, tone, mirror, turn, user_input, bot_response):
    try:
        sheet_id = SPREADSHEET_IDS[(mirror, tone)]
        worksheet = gc.open_by_key(sheet_id).sheet1
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        worksheet.append_row([pid, tone, mirror, turn, user_input, bot_response, timestamp])
    except Exception as e:
        logging.error(f"[Google Sheets Logging Error] {e}")
        st.warning("⚠️ 대화 기록 저장 중 오류 발생. 대화는 계속됩니다.")

if "initialized" not in st.session_state:
    tone, mirror, full, letter = allocate_condition()
    if full:
        st.error("모든 조건이 마감되었습니다.")
        st.stop()
    pid = letter + ''.join(random.choices(string.ascii_letters + string.digits, k=7))
    st.session_state.update({
        "pid": pid,
        "tone": tone,
        "mirror": mirror,
        "turn": 0,
        "history": [],
        "initialized": True,
        "questions": get_questions(tone),
        "used_questions_indices": [],
    })
    random.shuffle(st.session_state["questions"])
    st.session_state["q_idx"] = 0

st.title("🤖 대화 챗봇")
st.markdown(f"**참가자 ID:** {st.session_state['pid']}")
st.markdown(f"**남은 횟수: {15 - st.session_state['turn']}회**")
st.markdown("챗봇의 질문에만 해당하는 내용으로 답변해 주세요. **챗봇에게 질문하거나 단답으로 응답하는 것은 삼가주시기 바랍니다.**")

for m in st.session_state["history"]:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

user_input = st.chat_input("영화나 드라마에 대해 이야기해 주세요. (최대 100자)")
if user_input:
    if len(user_input) > 100:
        st.warning("입력은 100자 이하로 해주세요.")
        st.stop()

    normalized_input = user_input.strip().lower()
    if st.session_state["turn"] == 0 and any(greet in normalized_input for greet in ["안녕", "안녕하세요"]):
        reply = "안녕! 영화나 드라마 얘기 좀 해보자." if st.session_state["tone"] == "반말" else "안녕하세요! 영화나 드라마에 대해 이야기해 볼까요?"
        with st.chat_message("assistant"):
            st.markdown(reply)
        st.session_state["history"].append({"role": "assistant", "content": reply})
        st.stop()

    with st.chat_message("user"):
        st.markdown(user_input)
    st.session_state["history"].append({"role": "user", "content": user_input})

    tone = st.session_state["tone"]
    mirror = st.session_state["mirror"]

    if mirror == "술어 미러링":
        keywords = extract_keywords_via_gpt(user_input)
        available_questions = [
            q for i, q in enumerate(st.session_state["questions"]) if i not in st.session_state["used_questions_indices"]
        ]

        if not available_questions:
            st.session_state["questions"] = get_questions(tone)
            random.shuffle(st.session_state["questions"])
            st.session_state["used_questions_indices"] = []
            available_questions = st.session_state["questions"]

        question = random.choice(available_questions)
        question_index = st.session_state["questions"].index(question)
        st.session_state["used_questions_indices"].append(question_index)

        reply = generate_mirroring_sentence_only(user_input, tone == "존댓말", keywords, question)
    else:
        reply = st.session_state["questions"][st.session_state["q_idx"] % len(st.session_state["questions"])]

    with st.chat_message("assistant"):
        st.markdown(reply)
    st.session_state["history"].append({"role": "assistant", "content": reply})
    log_to_sheet(st.session_state["pid"], tone, mirror, st.session_state["turn"], user_input, reply)
    st.session_state["turn"] += 1
    st.session_state["q_idx"] += 1

    if st.session_state["turn"] == 15:
        st.warning("💡 대화 횟수가 1회 남았습니다.")
    if st.session_state["turn"] == 16:
        st.success("대화가 완료되었습니다. 설문으로 이동해주세요.")
        st.stop()
