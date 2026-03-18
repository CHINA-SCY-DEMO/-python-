import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor
import hashlib
import json
import os
import tempfile
import requests
import textwrap
import time
import random
import pandas as pd
from datetime import datetime
from typing import Dict, List, Tuple
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from contextlib import contextmanager

# ==================== Supabase PostgreSQL 配置 ====================

@contextmanager
def get_db_connection():
    """安全的数据库连接上下文管理器（自动处理连接池）"""
    conn = None
    try:
        # 从 Streamlit Secrets 读取，必须使用 Connection Pooler (6542端口)
        conn = psycopg2.connect(st.secrets["SUPABASE_DB_URL"])
        yield conn
    except Exception as e:
        st.error(f"数据库连接失败: {e}")
        raise
    finally:
        if conn:
            conn.close()

def init_db():
    """初始化数据库表（首次自动创建）"""
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            
            # 用户表
            c.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    password VARCHAR(64) NOT NULL,
                    email VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 简历记录表 - 使用 JSONB 存储结构化数据
            c.execute('''
                CREATE TABLE IF NOT EXISTS resumes (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    filename VARCHAR(255),
                    content TEXT,
                    structured_data JSONB,
                    score INTEGER,
                    total_score INTEGER,
                    score_details JSONB,
                    analysis JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 优化记录表
            c.execute('''
                CREATE TABLE IF NOT EXISTS optimizations (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    resume_id INTEGER REFERENCES resumes(id) ON DELETE CASCADE,
                    job_desc TEXT,
                    optimized_content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 创建索引加速查询
            c.execute('CREATE INDEX IF NOT EXISTS idx_resumes_user_id ON resumes(user_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_resumes_created ON resumes(created_at DESC)')
            
            conn.commit()
    except Exception as e:
        st.error(f"数据库初始化失败: {e}")

# 密码加密
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ==================== 数据库操作函数 ====================

def register_user(username, password, email):
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            hashed_pw = hash_password(password)
            c.execute(
                "INSERT INTO users (username, password, email) VALUES (%s, %s, %s) RETURNING id",
                (username, hashed_pw, email)
            )
            user_id = c.fetchone()[0]
            conn.commit()
            return True, "注册成功！", user_id
    except psycopg2.IntegrityError:
        return False, "用户名已存在", None
    except Exception as e:
        return False, f"注册失败: {str(e)}", None

def login_user(username, password):
    try:
        with get_db_connection() as conn:
            c = conn.cursor(cursor_factory=RealDictCursor)
            hashed_pw = hash_password(password)
            c.execute(
                "SELECT id, username FROM users WHERE username=%s AND password=%s",
                (username, hashed_pw)
            )
            result = c.fetchone()
            if result:
                return True, result['id'], result['username']
            return False, None, None
    except Exception as e:
        st.error(f"登录查询失败: {e}")
        return False, None, None

def save_resume(user_id, filename, content, structured_data, score, total_score, score_details, analysis):
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO resumes 
                (user_id, filename, content, structured_data, score, total_score, score_details, analysis) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                user_id, filename, content,
                json.dumps(structured_data, ensure_ascii=False),
                score, total_score,
                json.dumps(score_details, ensure_ascii=False),
                json.dumps(analysis, ensure_ascii=False)
            ))
            resume_id = c.fetchone()[0]
            conn.commit()
            return resume_id
    except Exception as e:
        st.error(f"保存简历失败: {e}")
        return None

def get_user_resumes(user_id):
    try:
        with get_db_connection() as conn:
            c = conn.cursor(cursor_factory=RealDictCursor)
            c.execute("""
                SELECT id, filename, score, total_score, TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI') as created_at
                FROM resumes 
                WHERE user_id=%s 
                ORDER BY created_at DESC
            """, (user_id,))
            results = c.fetchall()
            return [(r['id'], r['filename'], r['score'], r['total_score'], r['created_at']) for r in results]
    except Exception as e:
        st.error(f"获取历史记录失败: {e}")
        return []

def get_resume_detail(resume_id):
    try:
        with get_db_connection() as conn:
            c = conn.cursor(cursor_factory=RealDictCursor)
            c.execute("""
                SELECT content, structured_data, score, total_score, score_details, analysis 
                FROM resumes 
                WHERE id=%s
            """, (resume_id,))
            result = c.fetchone()
            if result:
                return {
                    'content': result['content'],
                    'structured_data': result['structured_data'] if result['structured_data'] else {},
                    'score': result['score'],
                    'total_score': result['total_score'],
                    'score_details': result['score_details'] if result['score_details'] else {},
                    'analysis': result['analysis'] if result['analysis'] else []
                }
            return None
    except Exception as e:
        st.error(f"获取详情失败: {e}")
        return None

def save_optimization(user_id, resume_id, job_desc, optimized_content):
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO optimizations (user_id, resume_id, job_desc, optimized_content) 
                VALUES (%s, %s, %s, %s)
            """, (user_id, resume_id, job_desc, optimized_content))
            conn.commit()
    except Exception as e:
        st.error(f"保存优化记录失败: {e}")

def get_user_stats(user_id):
    """获取用户统计数据（用于侧边栏）"""
    try:
        with get_db_connection() as conn:
            c = conn.cursor(cursor_factory=RealDictCursor)
            # 总简历数
            c.execute("SELECT COUNT(*) as count FROM resumes WHERE user_id=%s", (user_id,))
            total_count = c.fetchone()['count']
            
            # 平均完成度
            c.execute("""
                SELECT AVG(CAST(score AS FLOAT)/NULLIF(total_score,0)*100) as avg_pct 
                FROM resumes 
                WHERE user_id=%s
            """, (user_id,))
            avg_result = c.fetchone()
            avg_pct = avg_result['avg_pct'] if avg_result and avg_result['avg_pct'] else 0
            
            # 本月新增（使用 TO_CHAR 格式化）
            current_month = datetime.now().strftime("%Y-%m")
            c.execute("""
                SELECT COUNT(*) as count FROM resumes 
                WHERE user_id=%s AND TO_CHAR(created_at, 'YYYY-MM')=%s
            """, (user_id, current_month))
            month_count = c.fetchone()['count']
            
            # 最近活动时间
            c.execute("SELECT MAX(created_at) as last_time FROM resumes WHERE user_id=%s", (user_id,))
            last_time = c.fetchone()['last_time']
            
            return total_count, avg_pct, month_count, last_time
    except Exception as e:
        st.error(f"统计失败: {e}")
        return 0, 0, 0, None

def get_user_trend(user_id, limit=20):
    """获取趋势数据（用于图表）"""
    try:
        with get_db_connection() as conn:
            df = pd.read_sql_query("""
                SELECT created_at, 
                       ROUND(CAST(score AS FLOAT)/NULLIF(total_score,0)*100, 1) as percentage
                FROM resumes 
                WHERE user_id=%s 
                ORDER BY created_at ASC 
                LIMIT %s
            """, conn, params=(user_id, limit))
            return df
    except Exception as e:
        st.error(f"趋势数据获取失败: {e}")
        return pd.DataFrame()

def get_user_export_data(user_id):
    """获取导出数据"""
    try:
        with get_db_connection() as conn:
            df = pd.read_sql_query("""
                SELECT filename, score, total_score, 
                       ROUND(CAST(score AS FLOAT)/NULLIF(total_score,0)*100, 1) as completion_rate,
                       TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI') as created_at
                FROM resumes 
                WHERE user_id=%s 
                ORDER BY created_at DESC
            """, conn, params=(user_id,))
            return df
    except Exception as e:
        st.error(f"导出数据获取失败: {e}")
        return pd.DataFrame()

def clear_user_data(user_id):
    """清理用户数据"""
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM optimizations WHERE user_id=%s", (user_id,))
            c.execute("DELETE FROM resumes WHERE user_id=%s", (user_id,))
            conn.commit()
            return True
    except Exception as e:
        st.error(f"清理数据失败: {e}")
        return False

# ==================== AI 功能配置 ====================

# 配置切换：本地 Ollama vs 云端 API
USE_LOCAL_OLLAMA = os.getenv("USE_LOCAL_OLLAMA", "false").lower() == "true"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

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
    """使用Ollama Chat API进行对话"""
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
    except requests.exceptions.ConnectionError:
        st.error("🚨 无法连接到Ollama服务")
        return None
    except Exception as e:
        st.error(f"AI模型调用失败: {e}")
        return None

# ==================== 简历处理功能 ====================

import pdfplumber
import docx

def extract_text_from_pdf(file):
    """使用pdfplumber从PDF提取文本"""
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
        else:
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
    """使用AI解析简历文本"""
    messages = [
        {"role": "system", "content": RESUME_PARSE_PROMPT},
        {"role": "user", "content": f"请解析以下简历文本：\n\n{resume_text}"}
    ]
    for attempt in range(retries):
        try:
            response = call_ai_chat(messages, temperature=0.1)
            if response:
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
    return {
        "personal_info": {"name": "", "phone": "", "gender": "", "birth_date": "", "graduation_date": ""},
        "education": [], "internship": [], "activities": [], "projects": [],
        "certificates": [], "job_intention": {"target_position": ""}, "other_info": {"interests": "", "skills": ""}
    }

def calculate_resume_score(structured_data):
    """根据字段完整度计算简历评分"""
    score = 0
    score_details = {}

    pi = structured_data.get("personal_info", {})
    basic_fields = ["name", "phone", "gender", "birth_date", "graduation_date"]
    basic_score = sum(1 for field in basic_fields if pi.get(field))
    score += basic_score
    score_details["个人基本信息"] = {"得分": basic_score, "满分": 5}

    edu_list = structured_data.get("education", [])
    edu_score = sum(sum(1 for f in ["major", "school", "duration", "gpa", "honors"] if edu.get(f)) for edu in edu_list)
    score += edu_score
    score_details["教育经历"] = {"得分": edu_score, "满分": len(edu_list) * 5}

    intern_list = structured_data.get("internship", [])
    intern_score = sum(sum(1 for f in ["company", "position", "duration", "content"] if i.get(f)) for i in intern_list)
    score += intern_score
    score_details["实习经历"] = {"得分": intern_score, "满分": len(intern_list) * 4}

    act_list = structured_data.get("activities", [])
    act_score = sum(sum(1 for f in ["organization", "role", "duration", "content"] if a.get(f)) for a in act_list)
    score += act_score
    score_details["组织及活动经历"] = {"得分": act_score, "满分": len(act_list) * 4}

    proj_list = structured_data.get("projects", [])
    proj_score = sum(sum(1 for f in ["name", "duration", "content", "achievement"] if p.get(f)) for p in proj_list)
    score += proj_score
    score_details["项目经验"] = {"得分": proj_score, "满分": len(proj_list) * 4}

    cert_list = structured_data.get("certificates", [])
    cert_score = sum(sum(1 for f in ["name", "type", "level"] if c.get(f)) for c in cert_list)
    score += cert_score
    score_details["获奖证书"] = {"得分": cert_score, "满分": len(cert_list) * 3}

    ji = structured_data.get("job_intention", {})
    job_score = 1 if ji.get("target_position") else 0
    score += job_score
    score_details["求职意向"] = {"得分": job_score, "满分": 1}

    oi = structured_data.get("other_info", {})
    other_score = sum(1 for f in ["interests", "skills"] if oi.get(f))
    score += other_score
    score_details["其他信息"] = {"得分": other_score, "满分": 2}

    total = 5 + len(edu_list)*5 + len(intern_list)*4 + len(act_list)*4 + len(proj_list)*4 + len(cert_list)*3 + 1 + 2
    return score, score_details, total

def build_optimization_prompt(resume_text, job_desc):
    return f"""你是一位资深的猎头顾问。请根据目标职位招聘要求，优化我的简历。

具体要求：
1. **匹配关键词**：分析招聘要求中的核心技能，确保自然融入简历
2. **量化成果**：用数据和结果重写经历（例如，"使XX指标提升了XX%"）
3. **语言精炼**：使用专业、简洁的商务语言
4. **格式清晰**：按教育背景、工作经历、项目经验、技能特长组织

【原始简历内容】
{resume_text}

【目标职位招聘要求】
{job_desc}

请直接输出优化后的完整简历内容，不要包含额外解释。"""

def save_text_as_pdf(text, output_path):
    c = canvas.Canvas(output_path, pagesize=A4)
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
    y = A4[1] - top_margin

    for line in text.split('\n'):
        wrapped = textwrap.wrap(line, width=100) or ['']
        for part in wrapped:
            if y < top_margin:
                c.showPage()
                y = A4[1] - top_margin
                try:
                    c.setFont('SimHei' if 'SimHei' in c._fontname else 'Helvetica', 12)
                except:
                    c.setFont('Helvetica', 12)
            c.drawString(left_margin, y, part)
            y -= line_height
    c.save()

# ==================== Streamlit UI ====================

st.set_page_config(
    page_title="智能简历识别与优化系统",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stButton>button {
        width: 100%; border-radius: 20px; height: 3em;
        background-color: #4CAF50; color: white; font-weight: bold;
    }
    .stButton>button:hover { background-color: #45a049; }
    </style>
    """, unsafe_allow_html=True)

# 初始化数据库（应用启动时）
init_db()

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.user_id = None
    st.session_state.username = None

def show_auth_page():
    st.markdown("<div style='height: 50px;'></div>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<h2 style='text-align: center; color: #333;'>📄 智能简历系统</h2>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: #666;'>AI驱动的简历识别与优化</p>", unsafe_allow_html=True)
        
        tab1, tab2 = st.tabs(["🔑 登录", "📝 注册"])
        
        with tab1:
            with st.form("login_form"):
                username = st.text_input("用户名", placeholder="输入您的用户名")
                password = st.text_input("密码", type="password", placeholder="输入密码")
                submitted = st.form_submit_button("登录", use_container_width=True)
                if submitted and username and password:
                    success, user_id, uname = login_user(username, password)
                    if success:
                        st.session_state.authenticated = True
                        st.session_state.user_id = user_id
                        st.session_state.username = uname
                        st.success("登录成功！")
                        st.rerun()
                    else:
                        st.error("用户名或密码错误")
        
        with tab2:
            with st.form("register_form"):
                new_username = st.text_input("用户名", placeholder="设置用户名", key="reg_user")
                new_password = st.text_input("密码", type="password", placeholder="设置密码（至少6位）", key="reg_pass")
                email = st.text_input("邮箱", placeholder="用于找回密码", key="reg_email")
                submitted = st.form_submit_button("立即注册", use_container_width=True)
                if submitted:
                    if len(new_password) < 6:
                        st.warning("密码长度至少6位")
                    elif "@" not in email:
                        st.warning("请输入有效邮箱")
                    else:
                        success, msg, uid = register_user(new_username, new_password, email)
                        if success:
                            st.success(msg + "请登录")
                        else:
                            st.error(msg)

def display_resume_score(score, score_details, total):
    percentage = (score / total * 100) if total > 0 else 0
    color = "green" if percentage >= 80 else ("orange" if percentage >= 60 else "red")
    level = "优秀" if percentage >= 80 else ("良好" if percentage >= 60 else "待完善")
    advice = "简历质量很高" if percentage >= 80 else ("简历基本完整" if percentage >= 60 else "建议补充详细信息")
    
    st.markdown(f"""
    <div style="background-color: #f0f2f6; padding: 20px; border-radius: 10px; margin: 10px 0;">
        <h2 style="text-align: center; color: {color};">AI 简历评分: {score}/{total}</h2>
        <h4 style="text-align: center;">完成度: {percentage:.1f}% | 等级: {level}</h4>
        <p style="text-align: center; color: gray;">{advice}</p>
    </div>
    """, unsafe_allow_html=True)

    st.subheader("📊 详细评分")
    col1, col2 = st.columns(2)
    items = list(score_details.items())
    mid = len(items) // 2
    for i, (category, details) in enumerate(items):
        progress = details["得分"] / details["满分"] if details["满分"] > 0 else 0
        (col1 if i < mid else col2).write(f"**{category}**: {details['得分']}/{details['满分']}")
        (col1 if i < mid else col2).progress(progress)

def display_resume_summary(structured_data):
    col1, col2 = st.columns(2)
    with col1:
        st.write("**👤 基本信息**")
        pi = structured_data.get('personal_info', {})
        st.write(f"- 姓名: {pi.get('name', '未提取') or '未提取'}")
        st.write(f"- 电话: {pi.get('phone', '未提取') or '未提取'}")
    with col2:
        st.write("**📈 经历统计**")
        st.write(f"- 教育: {len(structured_data.get('education', []))} 条")
        st.write(f"- 实习: {len(structured_data.get('internship', []))} 条")

def show_main_app():
    with st.sidebar:
        st.markdown(f"### 👤 {st.session_state.username}")
        if st.button("🚪 退出登录", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()
        
        st.markdown("---")
        st.markdown("### 📊 我的数据概览")
        
        try:
            total_count, avg_pct, month_count, last_time = get_user_stats(st.session_state.user_id)
            c1, c2 = st.columns(2)
            c1.metric("📄 简历总数", total_count)
            c2.metric("⭐ 平均完成度", f"{avg_pct:.1f}%" if avg_pct else "0%")
            if month_count:
                st.caption(f"本月新增: {month_count} 份")
        except:
            pass
        
        st.markdown("### ⚡ 快捷操作")
        if 'show_trend' not in st.session_state:
            st.session_state.show_trend = False
        if 'show_export' not in st.session_state:
            st.session_state.show_export = False
            
        c1, c2 = st.columns(2)
        if c1.button("📈 趋势", use_container_width=True):
            st.session_state.show_trend = not st.session_state.show_trend
            st.session_state.show_export = False
        if c2.button("💾 导出", use_container_width=True):
            st.session_state.show_export = not st.session_state.show_export
            st.session_state.show_trend = False
        
        if st.session_state.show_trend:
            df = get_user_trend(st.session_state.user_id)
            if not df.empty and len(df) > 1:
                st.line_chart(df.set_index('created_at')['percentage'])
            else:
                st.info("数据不足")
                
        if st.session_state.show_export:
            df = get_user_export_data(st.session_state.user_id)
            if not df.empty:
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button("⬇️ CSV", csv, f"report_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")
        
        st.markdown("### 💡 提示")
        tips = ["PDF格式识别效果最佳", "粘贴完整JD匹配度更高", "定期导出备份数据"]
        st.info(tips[datetime.now().day % len(tips)])

    menu = st.radio("功能", ["📊 AI简历识别与打分", "✨ AI简历优化系统", "📜 历史记录"], horizontal=True)
    
    if menu == "📊 AI简历识别与打分":
        st.title("📊 AI智能简历识别与评分")
        c1, c2 = st.columns([1, 1])
        
        with c1:
            uploaded_file = st.file_uploader("上传简历 (PDF/DOCX)", type=['pdf', 'docx'])
            if uploaded_file:
                ext = uploaded_file.name.split('.')[-1].lower()
                content = extract_text_from_pdf(uploaded_file) if ext == 'pdf' else parse_docx(uploaded_file)
                
                if content and not content.startswith("解析错误"):
                    st.success("文本提取成功")
                    st.text_area("预览", content[:500] + "..." if len(content) > 500 else content, height=150)
                    
                    if st.button("🤖 AI解析与评分", type="primary"):
                        with st.spinner("解析中..."):
                            data = parse_resume_with_ollama(content)
                            score, details, total = calculate_resume_score(data)
                            st.session_state.update({
                                'current_structured_data': data, 'current_content': content,
                                'current_score': score, 'current_total': total,
                                'current_score_details': details, 'current_filename': uploaded_file.name,
                                'analysis_done': True
                            })
                            st.rerun()
                else:
                    st.error(content)
        
        with c2:
            if st.session_state.get('analysis_done'):
                display_resume_score(st.session_state['current_score'], st.session_state['current_score_details'], st.session_state['current_total'])
                with st.expander("查看详情"):
                    display_resume_summary(st.session_state['current_structured_data'])
                if st.button("💾 保存到历史"):
                    rid = save_resume(st.session_state.user_id, st.session_state['current_filename'],
                                    st.session_state['current_content'], st.session_state['current_structured_data'],
                                    st.session_state['current_score'], st.session_state['current_total'],
                                    st.session_state['current_score_details'], [])
                    if rid:
                        st.success(f"已保存 (ID: {rid})")
    
    elif menu == "✨ AI简历优化系统":
        st.title("✨ AI简历优化助手")
        source = st.radio("来源", ["历史记录", "上传新文件"], horizontal=True)
        
        content, resume_id = None, None
        if source == "历史记录":
            resumes = get_user_resumes(st.session_state.user_id)
            if resumes:
                opts = {f"{r[1]} ({r[2]}/{r[3]})": r[0] for r in resumes}
                sel = st.selectbox("选择简历", list(opts.keys()))
                if sel:
                    resume_id = opts[sel]
                    detail = get_resume_detail(resume_id)
                    if detail:
                        content = detail['content']
                        with st.expander("查看摘要"):
                            display_resume_summary(detail['structured_data'])
            else:
                st.warning("暂无历史记录")
        else:
            f = st.file_uploader("上传简历", type=['pdf', 'docx'])
            if f:
                content = extract_text_from_pdf(f) if f.name.endswith('.pdf') else parse_docx(f)
                if content and not content.startswith("解析错误"):
                    st.success("提取成功")
                    if st.checkbox("同时保存到历史"):
                        data = parse_resume_with_ollama(content)
                        score, details, total = calculate_resume_score(data)
                        resume_id = save_resume(st.session_state.user_id, f.name, content, data, score, total, details, [])
        
        if content:
            jd = st.text_area("输入目标岗位JD", height=200)
            if jd and st.button("🚀 开始AI优化"):
                with st.spinner("优化中..."):
                    prompt = build_optimization_prompt(content, jd)
                    opt_text = call_ai_chat([
                        {"role": "system", "content": "你是一位资深的猎头顾问，精通简历优化。"},
                        {"role": "user", "content": prompt}
                    ])
                    if opt_text:
                        st.session_state['optimized_text'] = opt_text
                        if resume_id:
                            save_optimization(st.session_state.user_id, resume_id, jd, opt_text)
            
            if st.session_state.get('optimized_text'):
                st.subheader("✨ 优化结果")
                st.text_area("优化后的简历", st.session_state['optimized_text'], height=400)
                c1, c2 = st.columns(2)
                c1.download_button("📥 TXT", st.session_state['optimized_text'].encode(), "optimized.txt", "text/plain")
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    save_text_as_pdf(st.session_state['optimized_text'], tmp.name)
                    with open(tmp.name, 'rb') as f:
                        c2.download_button("📥 PDF", f.read(), "optimized.pdf", "application/pdf")
                    os.unlink(tmp.name)
    
    else:
        st.title("📜 历史记录")
        resumes = get_user_resumes(st.session_state.user_id)
        if resumes:
            for rid, filename, score, total, created in resumes:
                with st.container():
                    c1, c2, c3 = st.columns([3, 2, 1])
                    c1.markdown(f"**📄 {filename}**")
                    pct = (score/total*100) if total else 0
                    c2.markdown(f"**{score}/{total}** ({pct:.0f}%)")
                    status = "优秀" if pct >= 80 else ("良好" if pct >= 60 else "待改进")
                    c3.markdown(f":{('green' if pct >= 80 else ('orange' if pct >= 60 else 'red'))}-badge[{status}]")
                    st.divider()
        else:
            st.info("暂无记录")

if not st.session_state.authenticated:
    show_auth_page()
else:
    show_main_app()
