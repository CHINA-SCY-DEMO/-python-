import streamlit as st
import sqlite3
import hashlib
import pdfplumber
import docx
import re
import json
import os
import tempfile
import requests
import textwrap
import time
from datetime import datetime
from typing import Dict, List, Tuple
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ==================== 数据库路径配置 ====================
# 支持通过环境变量 RESUME_DB_PATH 自定义路径，默认使用当前目录下的 data 文件夹
DB_PATH = os.getenv("RESUME_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "resume_system.db"))
# 确保数据目录存在
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
# =======================================================

# 配置切换：本地 Ollama vs 云端 API
USE_LOCAL_OLLAMA = os.getenv("USE_LOCAL_OLLAMA", "false").lower() == "true"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

def call_ai_chat(messages, temperature=0.1):
    """统一的AI调用接口"""
    if USE_LOCAL_OLLAMA:
        return call_ollama_chat(messages, temperature)
    else:
        return call_deepseek_api(messages, temperature)

def call_deepseek_api(messages, temperature=0.1):
    """调用 DeepSeek 官方 API（成本低，效果相当）"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "deepseek-chat",  # 或 deepseek-reasoner
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

# ==================== Ollama AI 配置 ====================
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "deepseek-r1:14b"
# =======================================================

# 数据库初始化
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 用户表
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  password TEXT NOT NULL,
                  email TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # 简历记录表 - 增加结构化数据字段
    c.execute('''CREATE TABLE IF NOT EXISTS resumes
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  filename TEXT,
                  content TEXT,
                  structured_data TEXT,
                  score INTEGER,
                  total_score INTEGER,
                  score_details TEXT,
                  analysis TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(id))''')
    
    # 优化记录表
    c.execute('''CREATE TABLE IF NOT EXISTS optimizations
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  resume_id INTEGER,
                  job_desc TEXT,
                  optimized_content TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(id),
                  FOREIGN KEY (resume_id) REFERENCES resumes(id))''')
    
    conn.commit()
    conn.close()

# 密码加密
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# 用户注册
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

# 用户登录
def login_user(username, password):
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

# 保存简历记录（增强版，保存结构化数据）
def save_resume(user_id, filename, content, structured_data, score, total_score, score_details, analysis):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO resumes (user_id, filename, content, structured_data, score, total_score, score_details, analysis) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
             (user_id, filename, content, 
              json.dumps(structured_data, ensure_ascii=False),
              score, total_score,
              json.dumps(score_details, ensure_ascii=False),
              json.dumps(analysis, ensure_ascii=False)))
    conn.commit()
    resume_id = c.lastrowid
    conn.close()
    return resume_id

# 获取用户简历历史
def get_user_resumes(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT id, filename, score, total_score, created_at 
                 FROM resumes WHERE user_id=? ORDER BY created_at DESC""",
             (user_id,))
    results = c.fetchall()
    conn.close()
    return results

# 获取单条简历详情
def get_resume_detail(resume_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT content, structured_data, score, total_score, score_details, analysis 
                 FROM resumes WHERE id=?""", (resume_id,))
    result = c.fetchone()
    conn.close()
    if result:
        return {
            'content': result[0],
            'structured_data': json.loads(result[1]) if result[1] else {},
            'score': result[2],
            'total_score': result[3],
            'score_details': json.loads(result[4]) if result[4] else {},
            'analysis': json.loads(result[5]) if result[5] else []
        }
    return None

# 保存优化记录
def save_optimization(user_id, resume_id, job_desc, optimized_content):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO optimizations (user_id, resume_id, job_desc, optimized_content) 
                 VALUES (?, ?, ?, ?)""",
             (user_id, resume_id, job_desc, optimized_content))
    conn.commit()
    conn.close()

# -------------------- AI 功能：文本提取 --------------------
def extract_text_from_pdf(file):
    """使用pdfplumber从PDF提取文本（支持文件对象或路径）"""
    try:
        # 如果是Streamlit上传的文件对象
        if hasattr(file, 'read'):
            file.seek(0)
            with pdfplumber.open(file) as pdf:
                text = ""
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                return text.strip()
        else:  # 如果是文件路径
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
    """解析Word文档"""
    try:
        doc = docx.Document(file)
        text = ""
        for para in doc.paragraphs:
            text += para.text + "\n"
        return text
    except Exception as e:
        return f"Word解析错误: {str(e)}"

# -------------------- AI 功能：Ollama调用 --------------------
def call_ollama_chat(messages, temperature=0.1):
    """使用Ollama Chat API进行对话"""
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 4096
        }
    }
    try:
        resp = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "")
    except requests.exceptions.ConnectionError:
        st.error("🚨 无法连接到Ollama服务，请确保：1. Ollama已安装 2. 运行 `ollama serve` 3. 已拉取模型 `ollama pull deepseek-r1:14b`")
        return None
    except Exception as e:
        st.error(f"AI模型调用失败: {e}")
        return None

# -------------------- AI 功能：简历解析 --------------------
RESUME_PARSE_PROMPT = """
你是一个专业的简历解析助手。请从简历文本中提取以下信息，并严格按照指定的JSON格式输出。

需要提取的信息分类和字段：
{
    "personal_info": {
        "name": "姓名",
        "phone": "电话",
        "gender": "性别",
        "birth_date": "出生日期",
        "graduation_date": "毕业时间"
    },
    "education": [
        {
            "major": "专业",
            "school": "学校",
            "duration": "学习时间",
            "gpa": "GPA",
            "honors": "荣誉"
        }
    ],
    "internship": [
        {
            "company": "公司名",
            "position": "职位",
            "duration": "时间",
            "content": "实习内容"
        }
    ],
    "activities": [
        {
            "organization": "组织名称",
            "role": "担任职务",
            "duration": "时间",
            "content": "组织活动内容"
        }
    ],
    "projects": [
        {
            "name": "项目名",
            "duration": "时间",
            "content": "项目内容",
            "achievement": "项目成功"
        }
    ],
    "certificates": [
        {
            "name": "证书名",
            "type": "类型",
            "level": "等级"
        }
    ],
    "job_intention": {
        "target_position": "企业岗位名"
    },
    "other_info": {
        "interests": "兴趣爱好",
        "skills": "技能"
    }
}

规则：
1. 所有字段都必须存在于输出JSON中
2. 如果某个字段在简历中不存在，使用空字符串或空数组
3. 数组字段（如education、internship）可以有多个条目
4. 请确保输出是合法的JSON格式，不要包含任何额外解释
5. 只输出JSON，不要输出markdown代码块标记
"""

def parse_resume_with_ollama(resume_text, retries=3):
    """使用Ollama本地模型解析简历文本"""
    messages = [
        {"role": "system", "content": RESUME_PARSE_PROMPT},
        {"role": "user", "content": f"请解析以下简历文本：\n\n{resume_text}"}
    ]
    
    for attempt in range(retries):
        try:
            response = call_ollama_chat(messages, temperature=0.1)
            if response:
                # 清理可能的markdown代码块
                response = response.strip()
                if response.startswith("```json"):
                    response = response[7:]
                if response.startswith("```"):
                    response = response[3:]
                if response.endswith("```"):
                    response = response[:-3]
                response = response.strip()
                
                return json.loads(response)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                return get_empty_structure()
    return get_empty_structure()

def get_empty_structure():
    """返回空的简历结构"""
    return {
        "personal_info": {"name": "", "phone": "", "gender": "", "birth_date": "", "graduation_date": ""},
        "education": [],
        "internship": [],
        "activities": [],
        "projects": [],
        "certificates": [],
        "job_intention": {"target_position": ""},
        "other_info": {"interests": "", "skills": ""}
    }

# -------------------- AI 功能：简历评分 --------------------
def calculate_resume_score(structured_data):
    """
    根据字段完整度计算简历评分（每个词条1分）
    返回: (总分, 详细评分, 满分)
    """
    score = 0
    score_details = {}

    # 1. 个人基本信息 (5分)
    pi = structured_data.get("personal_info", {})
    basic_fields = ["name", "phone", "gender", "birth_date", "graduation_date"]
    basic_score = sum(1 for field in basic_fields if pi.get(field))
    score += basic_score
    score_details["个人基本信息"] = {"得分": basic_score, "满分": 5}

    # 2. 教育经历 (5分/条)
    edu_list = structured_data.get("education", [])
    edu_score = 0
    for edu in edu_list:
        edu_fields = ["major", "school", "duration", "gpa", "honors"]
        edu_score += sum(1 for field in edu_fields if edu.get(field))
    score += edu_score
    score_details["教育经历"] = {"得分": edu_score, "满分": len(edu_list) * 5}

    # 3. 实习经历 (4分/条)
    intern_list = structured_data.get("internship", [])
    intern_score = 0
    for intern in intern_list:
        intern_fields = ["company", "position", "duration", "content"]
        intern_score += sum(1 for field in intern_fields if intern.get(field))
    score += intern_score
    score_details["实习经历"] = {"得分": intern_score, "满分": len(intern_list) * 4}

    # 4. 组织及活动经历 (4分/条)
    act_list = structured_data.get("activities", [])
    act_score = 0
    for act in act_list:
        act_fields = ["organization", "role", "duration", "content"]
        act_score += sum(1 for field in act_fields if act.get(field))
    score += act_score
    score_details["组织及活动经历"] = {"得分": act_score, "满分": len(act_list) * 4}

    # 5. 项目经验 (4分/条)
    proj_list = structured_data.get("projects", [])
    proj_score = 0
    for proj in proj_list:
        proj_fields = ["name", "duration", "content", "achievement"]
        proj_score += sum(1 for field in proj_fields if proj.get(field))
    score += proj_score
    score_details["项目经验"] = {"得分": proj_score, "满分": len(proj_list) * 4}

    # 6. 获奖证书 (3分/条)
    cert_list = structured_data.get("certificates", [])
    cert_score = 0
    for cert in cert_list:
        cert_fields = ["name", "type", "level"]
        cert_score += sum(1 for field in cert_fields if cert.get(field))
    score += cert_score
    score_details["获奖证书"] = {"得分": cert_score, "满分": len(cert_list) * 3}

    # 7. 求职意向 (1分)
    ji = structured_data.get("job_intention", {})
    job_score = 1 if ji.get("target_position") else 0
    score += job_score
    score_details["求职意向"] = {"得分": job_score, "满分": 1}

    # 8. 其他信息 (2分)
    oi = structured_data.get("other_info", {})
    other_fields = ["interests", "skills"]
    other_score = sum(1 for field in other_fields if oi.get(field))
    score += other_score
    score_details["其他信息"] = {"得分": other_score, "满分": 2}

    # 计算总分
    total = (5 +  # 个人基本信息
             len(edu_list) * 5 +  # 教育经历
             len(intern_list) * 4 +  # 实习经历
             len(act_list) * 4 +  # 组织及活动经历
             len(proj_list) * 4 +  # 项目经验
             len(cert_list) * 3 +  # 获奖证书
             1 +  # 求职意向
             2)   # 其他信息

    return score, score_details, total

# -------------------- AI 功能：简历优化 --------------------
def build_optimization_prompt(resume_text, job_desc):
    """构建简历优化提示词"""
    return f"""你是一位资深的猎头顾问，精通简历优化和职业规划。请根据我提供的【目标职位招聘要求】，帮我优化和完善我的【原始简历内容】。

具体要求：
1. **匹配关键词**：分析招聘要求中的核心技能和关键词，确保它们自然地融入我的简历描述中。
2. **量化成果**：用数据和结果来重写我的工作经历，让描述更具说服力（例如，将"负责XX"改为"通过XX方法，使XX指标提升了XX%"）。
3. **语言精炼**：使用专业、简洁的商务语言，去除冗余的词汇。
4. **格式清晰**：优化后的内容依然按【教育背景、工作经历、项目经验、技能特长】的版块组织。

【原始简历内容】
{resume_text}

【目标职位招聘要求】
{job_desc}

请直接输出优化后的完整简历内容，不要包含额外解释。
"""

def save_text_as_pdf(text, output_path):
    """将纯文本保存为PDF"""
    c = canvas.Canvas(output_path, pagesize=A4)
    width, height = A4

    # 尝试使用中文字体
    try:
        font_path = "C:/Windows/Fonts/simhei.ttf"
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont('SimHei', font_path))
            c.setFont('SimHei', 12)
        else:
            c.setFont('Helvetica', 12)
    except:
        c.setFont('Helvetica', 12)

    left_margin = 20 * mm
    top_margin = 20 * mm
    line_height = 5 * mm
    y = height - top_margin

    for line in text.split('\n'):
        wrapped = textwrap.wrap(line, width=100)
        if not wrapped:
            wrapped = ['']
        for part in wrapped:
            if y < top_margin:
                c.showPage()
                y = height - top_margin
                try:
                    c.setFont('SimHei' if 'SimHei' in c._fontname else 'Helvetica', 12)
                except:
                    c.setFont('Helvetica', 12)
            c.drawString(left_margin, y, part)
            y -= line_height
    c.save()

# 初始化数据库
init_db()

# Streamlit UI配置
st.set_page_config(
    page_title="智能简历识别与优化系统",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# CSS样式
st.markdown("""
    <style>
    .main {
        background-color: #f8f9fa;
    }
    .stButton>button {
        width: 100%;
        border-radius: 20px;
        height: 3em;
        background-color: #4CAF50;
        color: white;
        font-weight: bold;
    }
    .stButton>button:hover {
        background-color: #45a049;
    }
    .score-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 20px;
        border-radius: 15px;
        color: white;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .metric-card {
        background-color: white;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        margin-bottom: 10px;
    }
    .optimization-card {
        background-color: white;
        border-left: 5px solid #ff6b6b;
        padding: 15px;
        margin-bottom: 15px;
        border-radius: 0 10px 10px 0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .login-container {
        max-width: 400px;
        margin: 0 auto;
        padding: 40px;
        background-color: white;
        border-radius: 20px;
        box-shadow: 0 10px 25px rgba(0,0,0,0.1);
    }
    .scoring-rules {
        background-color: #f0f2f6;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 20px;
    }
    </style>
    """, unsafe_allow_html=True)

# 初始化session state
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.user_id = None
    st.session_state.username = None
    st.session_state.current_page = 'login'
    st.session_state.current_resume_id = None

# 登录/注册页面
def show_auth_page():
    st.markdown("<div style='height: 50px;'></div>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<div class='login-container'>", unsafe_allow_html=True)
        st.markdown("<h2 style='text-align: center; color: #333;'>📄 智能简历系统</h2>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: #666;'>AI驱动的简历识别与优化</p>", unsafe_allow_html=True)
        
        tab1, tab2 = st.tabs(["🔑 登录", "📝 注册"])
        
        with tab1:
            with st.form("login_form"):
                username = st.text_input("用户名", placeholder="输入您的用户名")
                password = st.text_input("密码", type="password", placeholder="输入密码")
                submitted = st.form_submit_button("登录", use_container_width=True)
                
                if submitted:
                    if username and password:
                        success, user_id, uname = login_user(username, password)
                        if success:
                            st.session_state.authenticated = True
                            st.session_state.user_id = user_id
                            st.session_state.username = uname
                            st.success("登录成功！")
                            st.rerun()
                        else:
                            st.error("用户名或密码错误")
                    else:
                        st.warning("请填写完整信息")
        
        with tab2:
            with st.form("register_form"):
                new_username = st.text_input("用户名", placeholder="设置用户名", key="reg_user")
                new_password = st.text_input("密码", type="password", placeholder="设置密码（至少6位）", key="reg_pass")
                email = st.text_input("邮箱", placeholder="用于找回密码", key="reg_email")
                submitted = st.form_submit_button("立即注册", use_container_width=True)
                
                if submitted:
                    if len(new_password) < 6:
                        st.warning("密码长度至少6位")
                    elif not email or "@" not in email:
                        st.warning("请输入有效邮箱")
                    else:
                        success, msg = register_user(new_username, new_password, email)
                        if success:
                            st.success(msg + "请登录")
                        else:
                            st.error(msg)
        
        st.markdown("</div>", unsafe_allow_html=True)

# AI 评分可视化
def display_resume_score(score, score_details, total):
    """可视化显示AI评分结果"""
    percentage = (score / total * 100) if total > 0 else 0

    if percentage >= 80:
        color = "green"
        level = "优秀"
        advice = "简历质量很高，内容完整度好"
    elif percentage >= 60:
        color = "orange"
        level = "良好"
        advice = "简历基本完整，建议补充缺失的字段"
    else:
        color = "red"
        level = "待完善"
        advice = "简历内容较单薄，建议补充更多详细信息"

    st.markdown(f"""
    <div style="background-color: #f0f2f6; padding: 20px; border-radius: 10px; margin: 10px 0;">
        <h2 style="text-align: center; color: {color};">AI 简历评分: {score}/{total}</h2>
        <h4 style="text-align: center;">完成度: {percentage:.1f}% | 等级: {level}</h4>
        <p style="text-align: center; color: gray;">{advice}</p>
    </div>
    """, unsafe_allow_html=True)

    # 显示各项详细得分
    st.subheader("📊 详细评分（每个词条1分）")
    col1, col2 = st.columns(2)
    items = list(score_details.items())
    mid = len(items) // 2

    with col1:
        for category, details in items[:mid]:
            progress = details["得分"] / details["满分"] if details["满分"] > 0 else 0
            st.write(f"**{category}**: {details['得分']}/{details['满分']}")
            st.progress(progress)

    with col2:
        for category, details in items[mid:]:
            progress = details["得分"] / details["满分"] if details["满分"] > 0 else 0
            st.write(f"**{category}**: {details['得分']}/{details['满分']}")
            st.progress(progress)

# 显示结构化数据摘要
def display_resume_summary(structured_data):
    """显示简历结构化摘要"""
    col1, col2 = st.columns(2)
    with col1:
        st.write("**👤 基本信息**")
        pi = structured_data.get('personal_info', {})
        st.write(f"- 姓名: {pi.get('name', '未提取') or '未提取'}")
        st.write(f"- 电话: {pi.get('phone', '未提取') or '未提取'}")
        st.write(f"- 性别: {pi.get('gender', '未提取') or '未提取'}")
        st.write(f"- 毕业时间: {pi.get('graduation_date', '未提取') or '未提取'}")
    with col2:
        st.write("**📈 经历统计**")
        st.write(f"- 教育经历: {len(structured_data.get('education', []))} 条")
        st.write(f"- 实习经历: {len(structured_data.get('internship', []))} 条")
        st.write(f"- 项目经验: {len(structured_data.get('projects', []))} 条")
        st.write(f"- 技能证书: {len(structured_data.get('certificates', []))} 条")

# 主应用界面
def show_main_app():
    # 侧边栏
    with st.sidebar:
        st.markdown(f"### 👤 当前用户: {st.session_state.username}")
        st.markdown("---")
        
        menu = st.radio("功能导航", ["📊 AI简历识别与打分", "✨ AI简历优化系统", "📜 历史记录"])
        
        if st.button("🚪 退出登录", use_container_width=True):
            st.session_state.authenticated = False
            st.session_state.user_id = None
            st.session_state.username = None
            st.rerun()
        
        st.markdown("---")
        st.markdown("### 💡 系统说明")
        st.info("本系统基于本地 DeepSeek 大模型\n\n支持格式：PDF、DOCX")
        
        # 检查Ollama连接状态
        try:
            resp = requests.post(OLLAMA_CHAT_URL, 
                               json={"model": MODEL_NAME, "messages": [], "stream": False}, 
                               timeout=3)
            st.success("🟢 AI服务正常")
        except:
            st.error("🔴 AI服务未连接")

    # ==================== AI简历识别与打分 ====================
    if menu == "📊 AI简历识别与打分":
        st.title("📊 AI智能简历识别与评分")
        st.markdown("**流程**: 上传简历 → AI解析 → 智能评分 → 保存记录")
        
        # 显示评分规则
        with st.expander("📖 查看AI评分规则详解", expanded=False):
            st.markdown("""
            ### 评分规则（每个词条1分）
            | 模块 | 字段 | 满分 |
            |------|------|------|
            | 个人基本信息 | 姓名、电话、性别、出生日期、毕业时间 | 5分 |
            | 教育经历 | 专业、学校、学习时间、GPA、荣誉 | 5分/条 |
            | 实习经历 | 公司名、职位、时间、实习内容 | 4分/条 |
            | 组织及活动经历 | 组织名称、担任职务、时间、组织活动内容 | 4分/条 |
            | 项目经验 | 项目名、时间、项目内容、项目成功 | 4分/条 |
            | 获奖证书 | 证书名、类型、等级 | 3分/条 |
            | 求职意向 | 企业岗位名 | 1分 |
            | 其他信息 | 兴趣爱好、技能 | 2分 |
            """)
        
        st.markdown("---")
        
        col1, col2 = st.columns([1, 1])
        
        with col1:
            st.subheader("📤 上传简历")
            uploaded_file = st.file_uploader("支持 PDF 或 Word 格式", type=['pdf', 'docx'])
            
            if uploaded_file:
                # 提取文本
                file_extension = uploaded_file.name.split('.')[-1].lower()
                with st.spinner("🔍 正在提取文本..."):
                    if file_extension == 'pdf':
                        content = extract_text_from_pdf(uploaded_file)
                    else:
                        content = parse_docx(uploaded_file)
                
                if content and not content.startswith("解析错误"):
                    st.success("✅ 文本提取成功")
                    st.text_area("提取的原始文本（前500字）", content[:500] + "..." if len(content) > 500 else content, height=150)
                    
                    # AI解析按钮
                    if st.button("🤖 开始AI解析与评分", type="primary"):
                        with st.spinner("🧠 AI正在深度解析简历结构..."):
                            structured_data = parse_resume_with_ollama(content)
                            score, score_details, total = calculate_resume_score(structured_data)
                            
                            # 保存到session state
                            st.session_state['current_structured_data'] = structured_data
                            st.session_state['current_content'] = content
                            st.session_state['current_score'] = score
                            st.session_state['current_total'] = total
                            st.session_state['current_score_details'] = score_details
                            st.session_state['current_filename'] = uploaded_file.name
                            st.session_state['analysis_done'] = True
                            st.rerun()
                else:
                    st.error(content)
        
        with col2:
            if st.session_state.get('analysis_done'):
                st.subheader("📋 AI解析结果")
                
                # 显示评分
                display_resume_score(
                    st.session_state['current_score'],
                    st.session_state['current_score_details'],
                    st.session_state['current_total']
                )
                
                # 显示结构化摘要
                with st.expander("查看结构化解析详情", expanded=True):
                    display_resume_summary(st.session_state['current_structured_data'])
                
                # 保存按钮
                if st.button("💾 保存分析结果到历史记录"):
                    resume_id = save_resume(
                        st.session_state.user_id,
                        st.session_state['current_filename'],
                        st.session_state['current_content'],
                        st.session_state['current_structured_data'],
                        st.session_state['current_score'],
                        st.session_state['current_total'],
                        st.session_state['current_score_details'],
                        []
                    )
                    st.session_state['current_resume_id'] = resume_id
                    st.success(f"✅ 已保存到历史记录 (ID: {resume_id})")

    # ==================== AI简历优化系统 ====================
    elif menu == "✨ AI简历优化系统":
        st.title("✨ AI简历优化助手")
        st.markdown("**流程**: 选择历史简历 或 上传新简历 → 输入目标岗位JD → AI优化 → 下载优化版")
        
        # 选择简历来源
        source = st.radio("选择简历来源", ["从历史记录选择", "上传新简历"], horizontal=True)
        
        content = None
        structured_data = None
        resume_id = None
        
        if source == "从历史记录选择":
            resumes = get_user_resumes(st.session_state.user_id)
            if resumes:
                options = {f"{r[1]} (评分: {r[2]}/{r[3]})": r[0] for r in resumes}
                selected = st.selectbox("选择要优化的简历", list(options.keys()))
                if selected:
                    resume_id = options[selected]
                    detail = get_resume_detail(resume_id)
                    if detail:
                        content = detail['content']
                        structured_data = detail['structured_data']
                        with st.expander("查看选中简历摘要"):
                            display_resume_summary(structured_data)
            else:
                st.warning("暂无历史记录，请上传新简历")
        
        else:  # 上传新简历
            uploaded_file = st.file_uploader("上传简历文件", type=['pdf', 'docx'])
            if uploaded_file:
                file_extension = uploaded_file.name.split('.')[-1].lower()
                with st.spinner("提取文本中..."):
                    if file_extension == 'pdf':
                        content = extract_text_from_pdf(uploaded_file)
                    else:
                        content = parse_docx(uploaded_file)
                
                if content and not content.startswith("解析错误"):
                    st.success("文本提取成功")
                    # 可选：先解析保存
                    if st.checkbox("同时保存到历史记录"):
                        with st.spinner("AI解析中..."):
                            structured_data = parse_resume_with_ollama(content)
                            score, score_details, total = calculate_resume_score(structured_data)
                            resume_id = save_resume(
                                st.session_state.user_id,
                                uploaded_file.name,
                                content, structured_data, score, total, score_details, []
                            )
                            st.success(f"已保存 (ID: {resume_id})")
        
        # 输入JD并优化
        if content:
            st.divider()
            st.subheader("🎯 输入目标岗位JD")
            job_desc = st.text_area(
                "请粘贴职位描述（岗位职责、任职要求等）",
                height=200,
                placeholder="例如：\n岗位职责：\n1. 负责前端开发...\n任职要求：\n1. 熟悉React...\n2. 3年以上经验..."
            )
            
            if job_desc.strip() and st.button("🚀 开始AI优化", type="primary"):
                with st.spinner("🧠 AI正在根据JD优化简历..."):
                    prompt = build_optimization_prompt(content, job_desc)
                    messages = [
                        {"role": "system", "content": "你是一位资深的猎头顾问，精通简历优化和职业规划。"},
                        {"role": "user", "content": prompt}
                    ]
                    
                    optimized_text = call_ollama_chat(messages)
                    
                    if optimized_text:
                        st.session_state['optimized_text'] = optimized_text
                        st.session_state['optimization_done'] = True
                        
                        # 如果有关联的resume_id，保存优化记录
                        if resume_id:
                            save_optimization(st.session_state.user_id, resume_id, job_desc, optimized_text)
            
            # 显示优化结果
            if st.session_state.get('optimization_done'):
                st.divider()
                st.subheader("✨ AI优化结果")
                
                tab1, tab2 = st.tabs(["优化后简历", "对比查看"])
                
                with tab1:
                    st.text_area("优化后的简历内容", st.session_state['optimized_text'], height=400)
                    
                    # 下载选项
                    col_dl1, col_dl2 = st.columns(2)
                    with col_dl1:
                        txt_bytes = st.session_state['optimized_text'].encode('utf-8')
                        st.download_button(
                            label="📥 下载为TXT",
                            data=txt_bytes,
                            file_name="optimized_resume.txt",
                            mime="text/plain"
                        )
                    with col_dl2:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                            save_text_as_pdf(st.session_state['optimized_text'], tmp.name)
                            tmp.seek(0)
                            pdf_bytes = tmp.read()
                        os.unlink(tmp.name)
                        st.download_button(
                            label="📥 下载为PDF",
                            data=pdf_bytes,
                            file_name="optimized_resume.pdf",
                            mime="application/pdf"
                        )
                
                with tab2:
                    col_orig, col_opt = st.columns(2)
                    with col_orig:
                        st.markdown("**原始简历**")
                        st.text_area("原始内容", content[:800] + "..." if len(content) > 800 else content, 
                                   height=400, disabled=True)
                    with col_opt:
                        st.markdown("**AI优化后**")
                        st.text_area("优化内容", st.session_state['optimized_text'][:800] + "...", 
                                   height=400, disabled=True)

    # ==================== 历史记录 ====================
    else:  # 历史记录
        st.title("📜 我的简历分析历史")
        st.markdown("---")
        
        resumes = get_user_resumes(st.session_state.user_id)
        
        if resumes:
            for resume in resumes:
                rid, filename, score, total_score, created_at = resume
                with st.container():
                    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
                    with col1:
                        st.markdown(f"**📄 {filename}**")
                        st.caption(f"分析时间：{created_at}")
                    with col2:
                        percentage = (score/total_score*100) if total_score > 0 else 0
                        st.markdown(f"**{score}/{total_score}** ({percentage:.0f}%)")
                    with col3:
                        if percentage >= 80:
                            st.success("优秀")
                        elif percentage >= 60:
                            st.info("良好")
                        else:
                            st.warning("待改进")
                    with col4:
                        if st.button("查看详情", key=f"view_{rid}"):
                            st.session_state[f"show_detail_{rid}"] = not st.session_state.get(f"show_detail_{rid}", False)
                    
                    # 详情展开
                    if st.session_state.get(f"show_detail_{rid}", False):
                        detail = get_resume_detail(rid)
                        if detail:
                            with st.expander("详细信息", expanded=True):
                                display_resume_score(detail['score'], detail['score_details'], detail['total_score'])
                                display_resume_summary(detail['structured_data'])
                                if st.button("加载此简历进行优化", key=f"opt_{rid}"):
                                    st.session_state.current_page = 'optimization'
                                    st.rerun()
                    
                    st.divider()
        else:
            st.info("暂无分析记录，快去上传第一份简历吧！")

# 主程序入口
if not st.session_state.authenticated:
    show_auth_page()
else:
    show_main_app()