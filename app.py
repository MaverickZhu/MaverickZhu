from flask import Flask, request, render_template, jsonify, send_from_directory
import os
import requests
import json
import pdfplumber  # 用于解析 PDF 文件
from docx import Document  # 用于解析 DOCX 文件
from werkzeug.utils import secure_filename  # 确保文件名安全
import re
import string
import unicodedata
import uuid  # 用于生成唯一文件名

app = Flask(__name__)

conversation_history = []

# 设置上传文件夹和允许的文件类型
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'doc'}  # 支持 txt、pdf、docx 和 doc 文件
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# 全局变量：存储用户上传的知识库内容
knowledge_base = {}


# 检查文件扩展名是否允许
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# 自定义文件名清理函数
def sanitize_filename(filename):
    allowed_chars = set("-_.() %s%s" % (string.ascii_letters, string.digits))
    cleaned_filename = ''.join(c for c in filename if c in allowed_chars or unicodedata.category(c).startswith('L'))
    cleaned_filename = cleaned_filename.replace(' ', '_')
    if not cleaned_filename:
        cleaned_filename = f"{uuid.uuid4().hex}.{''.join(os.path.splitext(filename)[1:])}"
    return cleaned_filename


# 提取 PDF 文件中的文本内容
def extract_text_from_pdf(pdf_path):
    if not os.path.exists(pdf_path):
        return f"Error: File not found at {pdf_path}"
    try:
        text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
        return text.strip()
    except Exception as e:
        return f"Error: Failed to read PDF file. {str(e)}"


# 提取 DOCX 文件中的文本内容
def extract_text_from_docx(docx_path):
    if not os.path.exists(docx_path):
        return f"Error: File not found at {docx_path}"
    try:
        doc = Document(docx_path)
        text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
        return text.strip()
    except Exception as e:
        return f"Error: Failed to read DOCX file. {str(e)}"


# 提取 DOC 文件中的文本内容
def extract_text_from_doc(doc_path):
    if not os.path.exists(doc_path):
        return f"Error: File not found at {doc_path}"
    try:
        import win32com.client
        word = win32com.client.Dispatch("Word.Application")
        doc = word.Documents.Open(doc_path)
        docx_path = os.path.splitext(doc_path)[0] + ".docx"
        doc.SaveAs(docx_path, FileFormat=16)
        doc.Close()
        word.Quit()

        # 提取 DOCX 文件中的文本
        return extract_text_from_docx(docx_path)
    except ImportError:
        return "Error: pywin32 is required to process .doc files on Windows."
    except Exception as e:
        return f"Error: Failed to process .doc file. {str(e)}"


# 扫描 uploads 文件夹并加载已有文件
def load_existing_files():
    global knowledge_base
    if os.path.exists(UPLOAD_FOLDER):
        for filename in os.listdir(UPLOAD_FOLDER):
            if allowed_file(filename):
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                print(f"Loading existing file: {filename}")

                if filename.lower().endswith('.pdf'):
                    content = extract_text_from_pdf(filepath)
                elif filename.lower().endswith('.docx'):
                    content = extract_text_from_docx(filepath)
                elif filename.lower().endswith('.doc'):
                    content = extract_text_from_doc(filepath)
                elif filename.lower().endswith('.txt'):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()

                if content:
                    knowledge_base[filename] = content


# 调用 Ollama 部署的 DeepSeek 模型进行推理
def query_deepseek(prompt):
    url = "http://localhost:11435/api/generate"
    payload = {
        "model": "deepseek-r1:32b",
        "prompt": prompt,
        "max_length": 4096,
        "temperature": 0.7
    }
    response = requests.post(url, json=payload, stream=True)

    answer = ""
    try:
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')

                try:
                    json_data = json.loads(decoded_line)
                    answer += json_data.get("response", "")

                    if json_data.get("done", False):
                        break
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        return f"Error: {str(e)}"

    formatted_answer = format_text(answer)
    return formatted_answer.strip()


# 文本格式化函数
def format_text(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace(". ", ".\n")
    text = text.replace("! ", "!\n")
    text = text.replace("? ", "?\n")
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# 主页路由
@app.route('/')
def index():
    return render_template('index.html', knowledge_base=knowledge_base)


# 文件上传路由
@app.route('/upload', methods=['POST'])
def upload_file():
    global knowledge_base
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    if file and allowed_file(file.filename):
        original_filename = file.filename
        filename = sanitize_filename(original_filename)
        print(f"Original File Name: {original_filename}")
        print(f"Sanitized File Name: {filename}")

        if not filename:
            return jsonify({"error": "Invalid file name after sanitization"}), 400

        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        print(f"File Path: {filepath}")

        # 如果文件已存在，则覆盖
        if filename in knowledge_base:
            print(f"Overwriting existing file: {filename}")

        file.save(filepath)

        # 根据文件类型提取内容
        if filename.lower().endswith('.pdf'):
            content = extract_text_from_pdf(filepath)
        elif filename.lower().endswith('.docx'):
            content = extract_text_from_docx(filepath)
        elif filename.lower().endswith('.doc'):
            content = extract_text_from_doc(filepath)
        elif filename.lower().endswith('.txt'):
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

        # 更新知识库
        knowledge_base[filename] = content

        return jsonify({"message": "File uploaded successfully!"}), 200
    else:
        return jsonify({"error": "Invalid file type"}), 400


# 删除知识库条目
@app.route('/delete/<filename>', methods=['DELETE'])
def delete_file(filename):
    global knowledge_base
    if filename in knowledge_base:
        # 删除文件
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"Deleted file: {filepath}")

        # 从知识库中移除
        del knowledge_base[filename]
        return jsonify({"message": f"File {filename} deleted successfully!"}), 200
    else:
        return jsonify({"error": "File not found in knowledge base."}), 404


# 清空知识库
@app.route('/clear', methods=['DELETE'])
def clear_knowledge_base():
    global knowledge_base
    # 删除所有文件
    for filename in list(knowledge_base.keys()):
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"Deleted file: {filepath}")

    # 清空知识库
    knowledge_base.clear()
    return jsonify({"message": "Knowledge base cleared successfully!"}), 200


# 查看知识库内容
@app.route('/view', methods=['GET'])
def view_knowledge_base():
    return jsonify(knowledge_base), 200


# 提问路由
@app.route('/ask', methods=['POST'])
def ask_question():
    global conversation_history
    try:
        print("Request received:", request.data.decode('utf-8'))  # 打印原始请求数据
        request_data = request.get_json()
        if not request_data:
            return jsonify({"error": "Invalid JSON data"}), 400

        question = request_data.get('question', '').strip()
        print("Parsed question:", question)  # 打印解析后的 question
        if not question:
            return jsonify({"error": "Question cannot be empty"}), 400

        combined_kb = "\n".join(knowledge_base.values())
        if combined_kb:
            prompt = f"Knowledge Base:\n{combined_kb}\n\nQuestion: {question}\nAnswer:"
        else:
            prompt = f"Question: {question}\nAnswer:"

        answer = query_deepseek(prompt)
        print("Generated answer:", answer)  # 打印生成的回答

        # 将对话内容存入历史记录
        conversation_history.append({"role": "user", "content": question})
        conversation_history.append({"role": "assistant", "content": answer})

        return jsonify({"answer": answer, "history": conversation_history}), 200

    except Exception as e:
        print("Error occurred:", str(e))  # 打印异常信息
        return jsonify({"error": str(e)}), 500


# 应用启动时加载已有文件
load_existing_files()

if __name__ == '__main__':
    app.run(debug=True)