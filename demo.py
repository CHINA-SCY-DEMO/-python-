import streamlit as st
import sqlite3
import hashlib
import json
import os
import tempfile
import requests
import textwrap
import time
from datetime import datetime
from typing import Dict, List, Tuple

# 必须在其他导入之前设置页面配置
st.set_page_config(
    page_title="智能简历识别与优化系统",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 数据库和API配置（必须在最前面）
# Streamlit Cloud 必须使用 /tmp 目录存储数据库
DB_PATH = os.getenv("RESUME_DB_PATH", "/tmp/resume_system.db")
USE_LOCAL_OLLAMA = os.getenv("USE_LOCAL_OLLAMA", "false").lower() == "true"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# 显示调试信息（开发时启用，生产时注释掉）
# st.write(f"Debug: DB_PATH={DB_PATH}, LocalOllama={USE_LOCAL_OLLAMA}, HasAPIKey={bool(DEEPSEEK_API_KEY)}")

# 延迟导入可能出问题的库（确保基础功能先加载）
try:
    import pdfplumber
    PDFPLUMBER_OK = True
except ImportError:
    PDFPLUMBER_OK = False
    st.error("pdfplumber 未安装")

try:
    import docx
    DOCX_OK = True
except ImportError:
    DOCX_OK = False
    st.error("python-docx 未安装")

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False

# ==================== AI API 配置 ====================
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "deepseek-r1:14b"

def call_ai_chat(messages, temperature=0.1):
    """统一的AI调用接口"""
    if USE_LOCAL_OLLAMA:
        return call_ollama_chat(messages, temperature)
    else:
        return call_deepseek_api(messages, temperature)

def call_deepseek_api(messages, temperature=0.1):
    """调用 DeepSeek 官方 API"""
    if not DEEPSEEK_API_KEY:
        st.error("未配置 DEEPSEEK_API_KEY，请在 Secrets 中设置")
        return None
    
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4096
    }
    
    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        st.error(f"API 调用失败: {e}")
        return None

def call_ollama_chat(messages, temperature=0.1):
    """使用Ollama Chat API"""
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": 4096}
    }
    try:
        resp = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "")
    except Exception as e:
        st.error(f"Ollama 连接失败: {e}")
        return None

# ==================== 数据库函数 ====================
def init_db():
    """初始化数据库"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS resumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            filename TEXT,
            content TEXT,
            structured_data TEXT,
            score INTEGER,
            total_score INTEGER,
            score_details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS optimizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            resume_id INTEGER,
            job_desc TEXT,
            optimized_content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"数据库初始化失败: {e}")
        return False

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username, password, email):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        hashed_pw = hash_password(password)
        c.execute("INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
                 (username, hashed_pw, email))
        conn.commit()
        conn.close()
        return True, "注册成功！"
    except sqlite3.IntegrityError:
        return False, "用户名已存在"
    except Exception as e:
        return False, f"注册失败: {str(e)}"

def login_user(username, password):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        hashed_pw = hash_password(password)
        c.execute("SELECT id, username FROM users WHERE username=? AND password=?",
                 (username, hashed_pw))
        result = c.fetchone()
        conn.close()
        if result:
            return True, result[0], result[1]
        return False, None, None
    except Exception as e:
        st.error(f"登录查询失败: {e}")
        return False, None, None

def save_resume(user_id, filename, content, structured_data, score, total_score, score_details):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""INSERT INTO resumes (user_id, filename, content, structured_data, score, total_score, score_details) 
                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                 (user_id, filename, content, 
                  json.dumps(structured_data, ensure_ascii=False),
                  score, total_score,
                  json.dumps(score_details, ensure_ascii=False)))
        conn.commit()
        resume_id = c.lastrowid
        conn.close()
        return resume_id
    except Exception as e:
        st.error(f"保存简历失败: {e}")
        return None

def get_user_resumes(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""SELECT id, filename, score, total_score, created_at 
                     FROM resumes WHERE user_id=? ORDER BY created_at DESC""",
                 (user_id,))
        results = c.fetchall()
        conn.close()
        return results
    except Exception as e:
        st.error(f"获取历史记录失败: {e}")
        return []

def get_resume_detail(resume_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""SELECT content, structured_data, score, total_score, score_details 
                     FROM resumes WHERE id=?""", (resume_id,))
        result = c.fetchone()
        conn.close()
        if result:
            return {
                'content': result[0],
                'structured_data': json.loads(result[1]) if result[1] else {},
                'score': result[2],
                'total_score': result[3],
                'score_details': json.loads(result[4]) if result[4] else {}
            }
        return None
    except Exception as e:
        st.error(f"获取详情失败: {e}")
        return None

def save_optimization(user_id, resume_id, job_desc, optimized_content):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""INSERT INTO optimizations (user_id, resume_id, job_desc, optimized_content) 
                     VALUES (?, ?, ?, ?)""",
                 (user_id, resume_id, job_desc, optimized_content))
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"保存优化记录失败: {e}")

# ==================== 简历处理功能 ====================
def extract_text_from_pdf(file):
    if not PDFPLUMBER_OK:
        return "错误: pdfplumber 库未安装"
    try:
        if hasattr(file, 'read'):
            file.seek(0)
            with pdfplumber.open(file) as pdf:
                text = ""
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                return text.strip()
    except Exception as e:
        return f"PDF解析错误: {str(e)}"

def parse_docx(file):
    if not DOCX_OK:
        return "错误: python-docx 库未安装"
    try:
        doc = docx.Document(file)
        text = ""
        for para in doc.paragraphs:
            text += para.text + "\n"
        return text
    except Exception as e:
        return f"Word解析错误: {str(e)}"

# ==================== AI 解析和评分 ====================
RESUME_PARSE_PROMPT = """你是一个专业的简历解析助手。请从简历文本中提取以下信息，并严格按照指定的JSON格式输出。

{
    "personal_info": {"name": "", "phone": "", "gender": "", "birth_date": "", "graduation_date": ""},
    "education": [{"major": "", "school": "", "duration": "", "gpa": "", "honors": ""}],
    "internship": [{"company": "", "position": "", "duration": "", "content": ""}],
    "activities": [{"organization": "", "role": "", "duration": "", "content": ""}],
    "projects": [{"name": "", "duration": "", "content": "", "achievement": ""}],
    "certificates": [{"name": "", "type": "", "level": ""}],
    "job_intention": {"target_position": ""},
    "other_info": {"interests": "", "skills": ""}
}

规则：
1. 所有字段必须存在，缺失的用空字符串或空数组
2. 只输出JSON，不要markdown代码块标记
3. 确保是合法的JSON格式"""

def parse_resume_with_ai(resume_text, retries=3):
    messages = [
        {"role": "system", "content": RESUME_PARSE_PROMPT},
        {"role": "user", "content": f"解析以下简历：\n\n{resume_text}"}
    ]
    
    for attempt in range(retries):
        response = call_ai_chat(messages, temperature=0.1)
        if response:
            try:
                # 清理markdown代码块
                response = response.strip()
                if response.startswith("```json"):
                    response = response[7:]
                if response.startswith("```"):
                    response = response[3:]
                if response.endswith("```"):
                    response = response[:-3]
                return json.loads(response.strip())
            except:
                if attempt == retries - 1:
                    return get_empty_structure()
                time.sleep(1)
    return get_empty_structure()

def get_empty_structure():
    return {
        "personal_info": {"name": "", "phone": "", "gender": "", "birth_date": "", "graduation_date": ""},
        "education": [], "internship": [], "activities": [],
        "projects": [], "certificates": [],
        "job_intention": {"target_position": ""},
        "other_info": {"interests": "", "skills": ""}
    }

def calculate_resume_score(structured_data):
    score = 0
    score_details = {}
    
    # 个人信息 5分
    pi = structured_data.get("personal_info", {})
    basic_score = sum(1 for f in ["name", "phone", "gender", "birth_date", "graduation_date"] if pi.get(f))
    score += basic_score
    score_details["个人基本信息"] = {"得分": basic_score, "满分": 5}
    
    # 教育经历 5分/条
    edu_list = structured_data.get("education", [])
    edu_score = sum(sum(1 for f in ["major", "school", "duration", "gpa", "honors"] if e.get(f)) for e in edu_list)
    score += edu_score
    score_details["教育经历"] = {"得分": edu_score, "满分": len(edu_list) * 5}
    
    # 实习经历 4分/条
    intern_list = structured_data.get("internship", [])
    intern_score = sum(sum(1 for f in ["company", "position", "duration", "content"] if i.get(f)) for i in intern_list)
    score += intern_score
    score_details["实习经历"] = {"得分": intern_score, "满分": len(intern_list) * 4}
    
    # 活动经历 4分/条
    act_list = structured_data.get("activities", [])
    act_score = sum(sum(1 for f in ["organization", "role", "duration", "content"] if a.get(f)) for a in act_list)
    score += act_score
    score_details["组织及活动经历"] = {"得分": act_score, "满分": len(act_list) * 4}
    
    # 项目经验 4分/条
    proj_list = structured_data.get("projects", [])
    proj_score = sum(sum(1 for f in ["name", "duration", "content", "achievement"] if p.get(f)) for p in proj_list)
    score += proj_score
    score_details["项目经验"] = {"得分": proj_score, "满分": len(proj_list) * 4}
    
    # 证书 3分/条
    cert_list = structured_data.get("certificates", [])
    cert_score = sum(sum(1 for f in ["name", "type", "level"] if c.get(f)) for c in cert_list)
    score += cert_score
    score_details["获奖证书"] = {"得分": cert_score, "满分": len(cert_list) * 3}
    
    # 求职意向 1分
    job_score = 1 if structured_data.get("job_intention", {}).get("target_position") else 0
    score += job_score
    score_details["求职意向"] = {"得分": job_score, "满分": 1}
    
    # 其他信息 2分
    oi = structured_data.get("other_info", {})
    other_score = sum(1 for f in ["interests", "skills"] if oi.get(f))
    score += other_score
    score_details["其他信息"] = {"得分": other_score, "满分": 2}
    
    total = 5 + len(edu_list)*5 + len(intern_list)*4 + len(act_list)*4 + len(proj_list)*4 + len(cert_list)*3 + 1 + 2
    return score, score_details, total

def build_optimization_prompt(resume_text, job_desc):
    return f"""你是一位资深猎头顾问。请根据【目标职位招聘要求】优化【原始简历】：

要求：
1. 匹配JD关键词，自然融入简历
2. 量化成果（用数据说话，如提升XX%）
3. 使用专业商务语言，去除冗余
4. 按教育背景、工作经历、项目经验、技能特长组织

【原始简历】
{resume_text}

【目标职位JD】
{job_desc}

直接输出优化后的完整简历，不要解释。"""

def save_text_as_pdf(text, output_path):
    if not REPORTLAB_OK:
        return False
    try:
        c = canvas.Canvas(output_path, pagesize=A4)
        width, height = A4
        
        # 尝试使用系统字体
        try:
            # Linux 常见中文字体路径
            font_paths = [
                "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
            ]
            font_set = False
            for fp in font_paths:
                if os.path.exists(fp):
                    try:
                        pdfmetrics.registerFont(TTFont('CustomFont', fp))
                        c.setFont('CustomFont', 12)
                        font_set = True
                        break
                    except:
                        continue
            if not font_set:
                c.setFont('Helvetica', 12)
        except:
            c.setFont('Helvetica', 12)
        
        left_margin = 20 * mm
        top_margin = 20 * mm
        line_height = 5 * mm
        y = height - top_margin
        
        for line in text.split('\n'):
            wrapped = textwrap.wrap(line, width=80) or ['']
            for part in wrapped:
                if y < top_margin:
                    c.showPage()
                    y = height - top_margin
                    c.setFont('Helvetica', 12)
                c.drawString(left_margin, y, part)
                y -= line_height
        c.save()
        return True
    except Exception as e:
        st.error(f"PDF生成失败: {e}")
        return False

# ==================== UI 部分 ====================
# 初始化数据库
if not init_db():
    st.error("系统初始化失败，请检查数据库权限")

# CSS样式
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stButton>button {
        width: 100%; border-radius: 20px; height: 3em;
        background-color: #4CAF50; color: white; font-weight: bold;
    }
    .stButton>button:hover { background-color: #45a049; }
    .score-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 20px; border-radius: 15px; color: white;
        text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .login-container {
        max-width: 400px; margin: 0 auto; padding: 40px;
        background-color: white; border-radius: 20px;
        box-shadow: 0 10px 25px rgba(0,0,0,0.1);
    }
    </style>
""", unsafe_allow_html=True)

# Session State 初始化
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.user_id = None
    st.session_state.username = None

def show_auth_page():
    st.markdown("<div style='height: 50px;'></div>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<div class='login-container'>", unsafe_allow_html=True)
        st.markdown("<h2 style='text-align: center;'>📄 智能简历系统</h2>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: #666;'>AI驱动的简历识别与优化</p>", unsafe_allow_html=True)
        
        tab1, tab2 = st.tabs(["🔑 登录", "📝 注册"])
        
        with tab1:
            with st.form("login_form"):
                username = st.text_input("用户名")
                password = st.text_input("密码", type="password")
                if st.form_submit_button("登录", use_container_width=True):
                    if username and password:
                        success, user_id, uname = login_user(username, password)
                        if success:
                            st.session_state.authenticated = True
                            st.session_state.user_id = user_id
                            st.session_state.username = uname
                            st.rerun()
                        else:
                            st.error("用户名或密码错误")
        
        with tab2:
            with st.form("register_form"):
                new_user = st.text_input("用户名", key="reg_user")
                new_pass = st.text_input("密码", type="password", key="reg_pass")
                email = st.text_input("邮箱", key="reg_email")
                if st.form_submit_button("注册", use_container_width=True):
                    if len(new_pass) < 6:
                        st.warning("密码至少6位")
                    elif "@" not in email:
                        st.warning("邮箱格式错误")
                    else:
                        success, msg = register_user(new_user, new_pass, email)
                        if success:
                            st.success(msg)
                        else:
                            st.error(msg)
        
        st.markdown("</div>", unsafe_allow_html=True)

def display_resume_score(score, score_details, total):
    percentage = (score / total * 100) if total > 0 else 0
    color = "green" if percentage >= 80 else "orange" if percentage >= 60 else "red"
    level = "优秀" if percentage >= 80 else "良好" if percentage >= 60 else "待完善"
    
    st.markdown(f"""
    <div style="background-color: #f0f2f6; padding: 20px; border-radius: 10px; margin: 10px 0;">
        <h2 style="text-align: center; color: {color};">AI 评分: {score}/{total}</h2>
        <h4 style="text-align: center;">完成度: {percentage:.1f}% | {level}</h4>
    </div>
    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    items = list(score_details.items())
    mid = len(items) // 2
    
    with col1:
        for cat, det in items[:mid]:
            prog = det["得分"] / det["满分"] if det["满分"] > 0 else 0
            st.write(f"**{cat}**: {det['得分']}/{det['满分']}")
            st.progress(prog)
    
    with col2:
        for cat, det in items[mid:]:
            prog = det["得分"] / det["满分"] if det["满分"] > 0 else 0
            st.write(f"**{cat}**: {det['得分']}/{det['满分']}")
            st.progress(prog)

def display_resume_summary(data):
    col1, col2 = st.columns(2)
    with col1:
        st.write("**👤 基本信息**")
        pi = data.get('personal_info', {})
        st.write(f"- 姓名: {pi.get('name', '未提取') or '未提取'}")
        st.write(f"- 电话: {pi.get('phone', '未提取') or '未提取'}")
    with col2:
        st.write("**📈 经历统计**")
        st.write(f"- 教育: {len(data.get('education', []))} 条")
        st.write(f"- 实习: {len(data.get('internship', []))} 条")
        st.write(f"- 项目: {len(data.get('projects', []))} 条")

def show_main_app():
    with st.sidebar:
        st.markdown(f"### 👤 {st.session_state.username}")
        menu = st.radio("功能", ["📊 简历识别与打分", "✨ 简历优化", "📜 历史记录"])
        
        if st.button("🚪 退出", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()
        
        st.markdown("---")
        if USE_LOCAL_OLLAMA:
            st.info("模式: 本地Ollama")
        else:
            st.info("模式: DeepSeek API")
    
    if menu == "📊 简历识别与打分":
        st.title("📊 AI简历识别与评分")
        
        with st.expander("📖 评分规则"):
            st.markdown("""
            | 模块 | 字段 | 满分 |
            |------|------|------|
            | 基本信息 | 姓名、电话、性别、出生日期、毕业时间 | 5分 |
            | 教育经历 | 专业、学校、时间、GPA、荣誉 | 5分/条 |
            | 实习经历 | 公司、职位、时间、内容 | 4分/条 |
            | 项目经验 | 名称、时间、内容、成果 | 4分/条 |
            | 获奖证书 | 名称、类型、等级 | 3分/条 |
            | 求职意向 | 目标岗位 | 1分 |
            """)
        
        col1, col2 = st.columns(2)
        
        with col1:
            uploaded = st.file_uploader("上传简历 (PDF/DOCX)", type=['pdf', 'docx'])
            if uploaded:
                ext = uploaded.name.split('.')[-1].lower()
                with st.spinner("提取文本..."):
                    if ext == 'pdf':
                        content = extract_text_from_pdf(uploaded)
                    else:
                        content = parse_docx(uploaded)
                
                if content and not content.startswith("错误"):
                    st.success("文本提取成功")
                    if st.button("🤖 开始AI解析", type="primary"):
                        with st.spinner("AI分析中..."):
                            data = parse_resume_with_ai(content)
                            score, details, total = calculate_resume_score(data)
                            
                            st.session_state['curr_data'] = data
                            st.session_state['curr_content'] = content
                            st.session_state['curr_score'] = score
                            st.session_state['curr_total'] = total
                            st.session_state['curr_details'] = details
                            st.session_state['curr_filename'] = uploaded.name
                            st.session_state['analysis_done'] = True
                            st.rerun()
                else:
                    st.error(content)
        
        with col2:
            if st.session_state.get('analysis_done'):
                display_resume_score(
                    st.session_state['curr_score'],
                    st.session_state['curr_details'],
                    st.session_state['curr_total']
                )
                
                with st.expander("查看详情"):
                    display_resume_summary(st.session_state['curr_data'])
                
                if st.button("💾 保存到历史记录"):
                    rid = save_resume(
                        st.session_state.user_id,
                        st.session_state['curr_filename'],
                        st.session_state['curr_content'],
                        st.session_state['curr_data'],
                        st.session_state['curr_score'],
                        st.session_state['curr_total'],
                        st.session_state['curr_details']
                    )
                    if rid:
                        st.success(f"已保存 (ID: {rid})")
    
    elif menu == "✨ 简历优化":
        st.title("✨ AI简历优化")
        
        source = st.radio("选择简历", ["历史记录", "上传新文件"], horizontal=True)
        
        content = None
        resume_id = None
        
        if source == "历史记录":
            resumes = get_user_resumes(st.session_state.user_id)
            if resumes:
                opts = {f"{r[1]} ({r[2]}/{r[3]}分)": r[0] for r in resumes}
                sel = st.selectbox("选择简历", list(opts.keys()))
                if sel:
                    resume_id = opts[sel]
                    det = get_resume_detail(resume_id)
                    if det:
                        content = det['content']
                        display_resume_summary(det['structured_data'])
            else:
                st.warning("暂无历史记录")
        else:
            uploaded = st.file_uploader("上传简历", type=['pdf', 'docx'])
            if uploaded:
                ext = uploaded.name.split('.')[-1].lower()
                content = extract_text_from_pdf(uploaded) if ext == 'pdf' else parse_docx(uploaded)
                if content and not content.startswith("错误"):
                    st.success("文件已读取")
        
        if content:
            jd = st.text_area("输入目标岗位JD", height=200)
            if jd and st.button("🚀 AI优化", type="primary"):
                with st.spinner("优化中..."):
                    prompt = build_optimization_prompt(content, jd)
                    msgs = [
                        {"role": "system", "content": "你是资深猎头顾问"},
                        {"role": "user", "content": prompt}
                    ]
                    result = call_ai_chat(msgs)
                    if result:
                        st.session_state['optimized'] = result
                        if resume_id:
                            save_optimization(st.session_state.user_id, resume_id, jd, result)
            
            if st.session_state.get('optimized'):
                st.subheader("优化结果")
                st.text_area("优化后的简历", st.session_state['optimized'], height=400)
                
                # 下载按钮
                col1, col2 = st.columns(2)
                with col1:
                    st.download_button("📥 下载TXT", 
                                     st.session_state['optimized'].encode('utf-8'),
                                     "optimized_resume.txt")
                with col2:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        if save_text_as_pdf(st.session_state['optimized'], tmp.name):
                            with open(tmp.name, 'rb') as f:
                                st.download_button("📥 下载PDF", f.read(), "optimized_resume.pdf")
                            os.unlink(tmp.name)
    
    else:  # 历史记录
        st.title("📜 历史记录")
        resumes = get_user_resumes(st.session_state.user_id)
        
        if resumes:
            for r in resumes:
                rid, fname, score, total, created = r
                pct = (score/total*100) if total > 0 else 0
                status = "🟢" if pct >= 80 else "🟡" if pct >= 60 else "🔴"
                
                with st.container():
                    c1, c2, c3 = st.columns([3, 2, 1])
                    with c1:
                        st.write(f"**{status} {fname}**")
                        st.caption(f"{created}")
                    with c2:
                        st.write(f"**{score}/{total}** ({pct:.0f}%)")
                    with c3:
                        if st.button("查看", key=f"view_{rid}"):
                            det = get_resume_detail(rid)
                            if det:
                                with st.expander("详情", expanded=True):
                                    display_resume_score(det['score'], det['score_details'], det['total_score'])
                                    display_resume_summary(det['structured_data'])
                st.divider()
        else:
            st.info("暂无记录")

# 主入口
if not st.session_state.authenticated:
    show_auth_page()
else:
    show_main_app()
