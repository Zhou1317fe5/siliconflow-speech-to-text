from flask import Flask, render_template, request, jsonify, Response, stream_with_context
import requests
import os
import time
import re
import json
from concurrent.futures import ThreadPoolExecutor
from waitress import serve

app = Flask(__name__, static_folder='static', static_url_path='')
 
# --- 服务配置 ---
S2T_API_URL = os.environ.get('S2T_API_URL', 'https://api.siliconflow.cn/v1/audio/transcriptions')
S2T_API_KEY = os.environ.get('S2T_API_KEY')
S2T_MODEL = os.environ.get('S2T_MODEL', 'FunAudioLLM/SenseVoiceSmall')

OPT_API_URL = os.environ.get('OPT_API_URL', 'https://api.openai.com/v1/chat/completions')
OPT_API_KEY = os.environ.get('OPT_API_KEY')
OPT_MODEL = os.environ.get('OPT_MODEL') # 默认模型

# --- 各功能专用模型配置（向后兼容）---
CALIBRATION_MODEL = os.environ.get('CALIBRATION_MODEL', OPT_MODEL) # 校准模型
SUMMARY_MODEL = os.environ.get('SUMMARY_MODEL', OPT_MODEL) # 摘要(量子速读)模型
NOTES_MODEL = os.environ.get('NOTES_MODEL', OPT_MODEL) # 笔记生成模型

# --- 分块处理配置 ---
CHUNK_TARGET_SIZE = 5000
CHUNK_PROCESSING_THRESHOLD = 5500
MAX_CONCURRENT_WORKERS = 3
RETRY_ATTEMPTS = 3

# --- OpenAI 兼容 API 配置 ---
API_ACCESS_TOKEN = os.environ.get('API_ACCESS_TOKEN')
MODEL_CALIBRATE = "s2t-calibrated"
MODEL_SUMMARIZE = "s2t-summarized"

# --- Prompts ---
HARDCODED_OPTIMIZATION_PROMPT = """
Description:
你是一位录音文字校准专家，能够消除口语表达中的停顿、重复和口语化语气词等常见问题，同时能解决录音软件在记录文字时会产生的错别字、多音字记录不准等技术问题，将口语化的录音文件转换为书面文字。
Background:
在录音转写过程中，录音软件经常会因为口音、语速等原因产生一些错别字、多音字记录不准等问题。
此外，口语表达中的停顿、重复和口语化语气词等也会影响文字的流畅和准确性。
为了将录音文件转写为高质量的书面文字，需要对这些问题进行校准和修正。
Goals:
1.消除录音文字中的停顿、重复和口语化语气词。
2.修正录音文字中的错别字和多音字。
3.确保转写文字保持原文完整性，不遗失任何细节。
4.提供高质量、流畅的书面文字版本。
Constraints:
1.保持原文的完整性，不得删除或遗漏任何信息。
2.仅校准，而不改写任何原文，确保文字内容忠实于原录音。
3.修正错误时需保证语义的准确性和连贯性。
4.校准过程中不得改变原文意思。
5.绝对不会对原文做概括和缩略性的总结，仅仅做出校对。
Skills:
熟悉录音转写的常见问题及其解决方法。
精通中文，能够识别并修正错别字和多音字。
具有良好的语言表达能力，能够将口语化的内容转换为流畅的书面文字。
具备细致入微的校对能力，确保文字的准确性和完整性。
Workflows:
输入: 根据用户提交的录音转化为文字稿。
校准: 消除停顿、重复和口语化语气词，修正错别字和多音字。
输出: 提供校准后的高质量书面文字版本。
检查: 确认修正后的文字保持原文完整性和准确性。
"""

PROMPT_SUMMARY_MAP = """
Description:
你是一位信息分析专家，正在执行一个大型文档分析任务的第一步。你的当前任务是，从提供给你的【文档片段】中，高效、精准地提取出所有的核心信息和关键要点。你提取出的要点将作为后续最终摘要整合的【原材料】。
Skills：
1.提炼关键信息：快速识别并提取关键词、主题句或中心思想。
2.逻辑梳理：理解文字的内在逻辑，确保提取的要点条理清晰。
3.分类归类：能够将片段内的相关信息点进行有效归纳。
4.概括提升：在归纳的基础上，提炼出更高层次的概念或结论。
5.精简语言：使用最凝练的语言来表达要点，去除所有不必要的修饰和口语化内容。
Rules：
1.准确全面：提取的要点必须准确反映【文档片段】的核心内容，不能遗漏重要信息。
2.客观中立：仅提取事实和观点，不掺杂个人情绪或进行二次解读。
3.重点突出：把握住片段的重点和主旨，突出核心内容。
Workflows：
1. 仔细阅读用户给出的【文档片段】原文。
2. 根据 <Rules> 对原文进行分析，提取出所有关键信息点。
3. 将这些关键信息点整理成一个无序列表（bullet points）进行输出。
Output Format:
- [要点1]
- [要点2]
- [要点3]
Constraints:
1. 忠于原文，不得改变【文档片段】的原意。
2. 只输出要点列表，不要输出任何额外的标题、开头、结尾、解释或说明。例如，不要写“以下是本文档片段的要点：”。
3. 每个要点都应是独立的、有意义的信息单元。
"""

PROMPT_SUMMARY_REDUCE = """
Description:
你是一位资深的首席编辑，你的任务是将一份由多位助理从一篇长篇录音稿中分段提取的【核心要点清单】进行最终整合，撰写成一份完整、流畅、逻辑清晰的最终摘要，供高层决策者阅读。
Background:
你收到的【核心要点清单】是按原文顺序排列的，但因为是分段提取，所以可能存在轻微的重复、风格不一或上下文断裂的问题。你的专业价值在于消除这些问题，创造出一篇浑然一体的最终作品。
Skills：
1. 全局视野：能够从零散的要点中洞察整个文档的宏观结构和核心主旨。
2. 叙事构建：擅长将独立的要点有机地串联起来，构建出有开头、有主体、有结尾的流畅叙事。
3. 逻辑重构：识别并梳理要点之间的逻辑关系（因果、并列、递进等），形成严密的逻辑链条。
4. 语言润色：使用专业、精炼、书面化的语言，统一全文风格，提升文本的专业性和可读性。
5. 综合概括：在整合所有要点的基础上，进行更高层次的归纳与升华。
Rules：
1. 全面覆盖：最终摘要必须全面反映【核心要点清单】中提供的所有关键信息，不得遗漏。
2. 连贯流畅：摘要的段落和句子之间必须过渡自然，逻辑通顺。
3. 结构清晰：摘要应有引言部分概括主旨，主体部分分点或分段阐述，结论部分进行总结。
4. 客观准确：忠实于要点清单的内容，不添加清单之外的信息或个人主观臆断。
Workflows：
1. 通读并理解整个【核心要点清单】。
2. 在脑海中构思出最终摘要的宏观结构。
3. 开始撰写，将独立的要点无缝地融入到流畅的段落中。
4. 撰写完成后，进行通篇审阅，检查逻辑、流畅性和完整性。
5. 输出最终的、可以直接发布的摘要成品，不要输出任何额外的解释或说明。
Constraints:
1. 绝对不要在摘要中提及“根据提供的要点”、“此清单显示”或“本文档的要点是”这类元语言。你必须让读者感觉，这份摘要是你直接阅读了全文后撰写的。
2. 不要对要点进行简单的罗列或堆砌。你的核心任务是【整合】与【重构】。
3. 输出格式应为一篇格式化良好、适合人类阅读的完整文章。
"""

PROMPT_GENERATE_NOTES = """
我是一名世界顶尖学术机构专精于定性数据分析的高级研究助理，能够将用户提供的非结构化文本（如视频转录稿等）转化为结构清晰、信息密集的学术笔记。我的分析过程严谨、确定，推理温度设定在0.2，以确保最高水平的学术准确性和逻辑严密性。

我处理的所有文本都必须被包裹在`<待处理文本>`标签内。

我的核心任务是深度提炼、整合并构建信息，最终生成一份逻辑严谨、可直接用于学术报告或研究论文的高质量中文笔记。为此，我遵循一个严格的三阶段自我校对工作流程：

**第一阶段：草稿生成**
我会根据以下步骤，创建一份初步的学术笔记草稿：
1. **核心论点提炼**：首先，用一句话精准概括所提供文本的核心论点或研究问题，确立整篇笔记的主线。
2. **深度分析与主题识别**：通读并分析全部内容，识别出贯穿全文的核心主题、主要论点和基本逻辑框架，剔除所有广告、口头禅、无关闲聊等干扰信息。
3. **结构化信息重组**：在理解整体结构后，将所有相关信息进行逻辑分组和层级归类。主动识别并整理内容中的核心概念、关键论据、支撑数据、案例分析、数学公式和重要结论，将它们置于最合适的逻辑层级之下。
4. **精炼总结与量化提炼**：在结构化基础上，对每个信息组进行综合性提炼与总结。每个子主题下的核心要点总结控制在50字以内，以最精确、简洁的学术语言概括其实质内容。

**第二阶段：自我批判与审查**
生成草稿后，我会暂停并启动内部审查机制。从批判性学者的角度，全面审视草稿的：
- **逻辑连贯性**：检查论证链条的完整性和推理的严密性
- **信息密度**：确保每个段落都具有高信息价值，无冗余表达
- **结构清晰度**：验证层次分明，逻辑流畅
- **学术规范性**：检查语言风格、术语使用和格式要求的一致性

识别出任何潜在的逻辑漏洞、总结不足或结构不清之处。

**第三阶段：最终版本输出**
基于自我审查结果，重写并优化草稿，生成最终的精炼版本。

我的最终输出必须严格遵守以下规范：

**格式要求：**
- 所有输出采用层级分明的Markdown格式，使用标准标题层次（#, ##, ###）、嵌套列表（-）和粗体（**）构建清晰的视觉和逻辑结构
- 所有编号主题统一使用H2格式：`## 1. 主题名称`，避免渲染错误
- 数学公式必须使用标准LaTeX语法，确保Markdown环境正确渲染
- 布局优化,通过项目符号、表格和简洁段落增强可读性
-
**语调与风格：**
- 始终保持专业、客观、正式的学术语调
- 语言力求精确、简洁、无歧义，避免口语化或主观性表达
- 专有名词、技术术语、品牌名称、人名保留英文原文以确保准确性

**内容焦点：**
- 聚焦信息实质内容，突出核心概念、关键论据、支撑数据和最终结论
- 确保笔记具有极高的信息密度和学术价值
- 保留所有重要的计算过程、推导步骤和量化分析

**交付物：**
- 输出必须直接以一级Markdown标题（#）开始，严格遵循所构建的笔记结构
- 纯Markdown文本输出，不使用任何代码块包裹

现在，请在`<待处理文本>`标签内提供您的视频转录内容，我将严格按照以上三阶段工作流程为您生成高质量的学术笔记。
"""

# --- 辅助函数 ---
def _extract_api_error_message(response):
    try:
        error_detail = response.json()
        if isinstance(error_detail, dict):
            api_err_msg = error_detail.get('error', {}).get('message') or error_detail.get('message') or error_detail.get('detail')
            if api_err_msg: return str(api_err_msg)
        
        # 如果响应是JSON编码的字符串或其他非字典类型，直接返回其文本表示
        return response.text[:200]
    except ValueError:
        # 如果响应不是有效的JSON，返回其文本内容
        return response.text[:200]

# 智能分块策略函数
def _split_text_intelligently(text, chunk_size=CHUNK_TARGET_SIZE):
    '''
    目标大小：CHUNK_TARGET_SIZE = 5000字符
    分割策略：优先在句号、感叹号、问号、换行符处切分
    边界处理：避免在词汇中间切断
    '''
    if not text or len(text) <= chunk_size:
        return [text] if text else []
    delimiters = ['。', '！', '？', '\n']
    chunks = []
    start_index = 0
    while start_index < len(text):
        end_index = start_index + chunk_size
        if end_index >= len(text):
            chunks.append(text[start_index:])
            break
        best_split_pos = -1
        for d in delimiters:
            pos = text.rfind(d, start_index, end_index)
            if pos > best_split_pos: best_split_pos = pos
        if best_split_pos != -1:
            chunks.append(text[start_index : best_split_pos + 1])
            start_index = best_split_pos + 1
        else:
            chunks.append(text[start_index:end_index])
            start_index = end_index
    return [c for c in chunks if c.strip()]

def _get_last_sentence(text):
    if not text: return ""
    sentences = re.split(r'(?<=[。？！\n])', text.strip())
    return sentences[-1].strip() if sentences else ""

def _optimize_chunk_with_retry(chunk_data):
    text_chunk = chunk_data['text']
    context_sentence = chunk_data.get('context')
    chunk_index = chunk_data.get('index', 1)
    total_chunks = chunk_data.get('total', 1)
    messages = [{"role": "system", "content": HARDCODED_OPTIMIZATION_PROMPT}]
    if context_sentence:
        user_content = (f"为了保持上下文连贯，这是紧接在当前文本之前的最后一句话：\n---CONTEXT---\n{context_sentence}\n---END CONTEXT---\n\n"
                        f"请仅校准并返回以下这段新的文本，不要在你的回答中重复上面的上下文内容：\n---TEXT TO CALIBRATE---\n{text_chunk}\n---END TEXT---")
    else:
        user_content = text_chunk
    messages.append({"role": "user", "content": user_content})
    payload = {'model': CALIBRATION_MODEL, 'messages': messages, 'temperature': 0.1}
    headers = {'Authorization': f'Bearer {OPT_API_KEY}', 'Content-Type': 'application/json'}
    
    # 重试机制，带有详细日志输出
    for attempt in range(RETRY_ATTEMPTS):
        log_prefix = f"块 {chunk_index}/{total_chunks}"
        try:
            print(f"校准{log_prefix} (尝试 {attempt + 1}/{RETRY_ATTEMPTS})...")
            response = requests.post(OPT_API_URL, headers=headers, json=payload, timeout=300)
            
            if response.status_code == 200:
                data = response.json()
                content = data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
                if content:
                    print(f"{log_prefix} 校准成功。")
                    return {"status": "success", "content": content}
                else:
                    return {"status": "error", "message": "API返回空内容"}
            else:
                error_msg = f"API错误 {response.status_code}: {_extract_api_error_message(response)}"
                # 对于客户端错误，不进行重试
                if response.status_code in [400, 401, 403, 429]:
                    return {"status": "error", "message": error_msg}
                
        except requests.exceptions.Timeout:
            error_msg = "请求超时"
        except requests.exceptions.RequestException as e:
            error_msg = f"网络连接错误: {type(e).__name__}"
        except Exception as e:
            error_msg = f"未知错误: {str(e)}"
        
        # 如果是最后一次重试，返回错误
        if attempt == RETRY_ATTEMPTS - 1:
            print(f"{log_prefix} 校准失败: {error_msg}")
            return {"status": "error", "message": error_msg}
        
        # 重试前等待
        wait_time = 2 * (attempt + 1)
        print(f"{log_prefix} 校准失败，{wait_time}秒后重试: {error_msg}")
        time.sleep(wait_time)
    
    return {"status": "error", "message": "未知错误，已达最大重试次数"}

def _perform_text_optimization(raw_text_to_optimize):
    opt_configured_properly = OPT_API_KEY and OPT_API_URL and OPT_API_URL.startswith(('http://', 'https://')) and CALIBRATION_MODEL
    if not opt_configured_properly:
        opt_configured_for_check = OPT_API_KEY or (OPT_API_URL and OPT_API_URL != 'https://api.openai.com/v1/chat/completions') or CALIBRATION_MODEL
        skip_reason_parts = []
        if not OPT_API_KEY: skip_reason_parts.append("缺少API Key")
        if not OPT_API_URL or not OPT_API_URL.startswith(('http://', 'https://')): skip_reason_parts.append("API URL无效")
        if not CALIBRATION_MODEL: skip_reason_parts.append("缺少校准模型名称")
        if not opt_configured_for_check:
             opt_status_message = "校准已跳过 (服务未配置)"
        else:
            skip_reason = ", ".join(skip_reason_parts)
            opt_status_message = f"校准已跳过 (服务配置不完整: {skip_reason})"
        print(f"OPT API 配置不完整，跳过文本优化。原因: {opt_status_message}")
        return raw_text_to_optimize, opt_status_message, False

    if len(raw_text_to_optimize) <= CHUNK_PROCESSING_THRESHOLD:
        print("文本较短，直接进行单次校准...")
        result = _optimize_chunk_with_retry({'text': raw_text_to_optimize, 'index': 1, 'total': 1})
        if result['status'] == 'success':
            return result['content'], "校准成功！", True
        else:
            return raw_text_to_optimize, f"校准失败 ({result['message']})", False
            
    chunks = _split_text_intelligently(raw_text_to_optimize)
    total_chunks = len(chunks)
    print(f"文本过长({len(raw_text_to_optimize)}字)，启动分块并发校准 (共 {total_chunks} 块)...")
    tasks = [{'text': chunk, 'context': (_get_last_sentence(chunks[i-1]) if i > 0 else None), 'index': i + 1, 'total': total_chunks} for i, chunk in enumerate(chunks)]
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS) as executor:
        processed_results = list(executor.map(_optimize_chunk_with_retry, tasks))
    failed_chunks = [res for res in processed_results if res['status'] == 'error']
    if failed_chunks:
        first_error_message = failed_chunks[0]['message']
        print(f"校准过程中有 {len(failed_chunks)}/{total_chunks} 个块处理失败，回退到原始文本。首个失败原因: {first_error_message}")
        return raw_text_to_optimize, f"校准失败 ({first_error_message})", False
    else:
        full_optimized_text = "".join([res['content'] for res in processed_results])
        print(f"所有 {total_chunks} 个块均已成功校准并合并。")
        return full_optimized_text, "校准成功！", True

# 要点提取逻辑函数
def _summarize_chunk_with_retry(text_chunk):
    '''
    使用PROMPT_SUMMARY_MAP prompt
    对每个文本块提取关键信息点
    输出格式：无序列表(bullet points)
    重试机制：失败时重试最多3次
    '''
    messages = [{"role": "system", "content": PROMPT_SUMMARY_MAP}, {"role": "user", "content": text_chunk}]
    payload = {'model': SUMMARY_MODEL, 'messages': messages, 'temperature': 0.1}
    headers = {'Authorization': f'Bearer {OPT_API_KEY}', 'Content-Type': 'application/json'}
    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = requests.post(OPT_API_URL, headers=headers, json=payload, timeout=300)
            if response.status_code == 200:
                data = response.json()
                content = data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
                if content: return {"status": "success", "content": content}
                else: return {"status": "error", "message": "API为Map阶段返回空内容"}
            error_msg = f"API错误 {response.status_code}: {_extract_api_error_message(response)}"
            if response.status_code in [400, 401, 403, 429]: return {"status": "error", "message": error_msg}
        except requests.exceptions.Timeout: error_msg = "请求超时"
        except requests.exceptions.RequestException as e: error_msg = f"网络连接错误: {type(e).__name__}"
        if attempt == RETRY_ATTEMPTS - 1: return {"status": "error", "message": error_msg}
        time.sleep(2 * (attempt + 1))
    return {"status": "error", "message": "未知错误，已达最大重试次数"}

# 后端核心处理流程
def _perform_summarization(text_to_summarize):
    print("开始总结任务: Map 阶段 - 并发提取要点...")
    # Step 1: 智能文本分块（_split_text_intelligently()）
    chunks = _split_text_intelligently(text_to_summarize)
    if not chunks: return {"status": "error", "message": "待总结文本为空或分割失败"}
    # Step 2: Map阶段 - 并发要点提取（_summarize_chunk_with_retry()），按CHUNK_TARGET_SIZE（默认5000）字符分块，并发处理每个文本块
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS) as executor:
        map_results = list(executor.map(_summarize_chunk_with_retry, chunks))
    failed_chunks = [res for res in map_results if res['status'] == 'error']
    if failed_chunks:
        first_error = failed_chunks[0]['message']
        return {"status": "error", "message": f"提取要点失败 ({first_error})"}
    print("Map 阶段成功。开始 Reduce 阶段 - 整合生成最终摘要...")
    # Step 3: Reduce阶段 - 整合最终摘要
    ## 合并所有要点
    combined_points = "\n\n".join([res['content'] for res in map_results])
    ## 使用PROMPT_SUMMARY_REDUCE prompt 进行最终整合
    messages = [{"role": "system", "content": PROMPT_SUMMARY_REDUCE}, {"role": "user", "content": combined_points}]
    payload = {'model': SUMMARY_MODEL, 'messages': messages, 'temperature': 0.2}
    headers = {'Authorization': f'Bearer {OPT_API_KEY}', 'Content-Type': 'application/json'}
    
    # 添加重试机制，与其他功能保持一致
    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = requests.post(OPT_API_URL, headers=headers, json=payload, timeout=300)
            if response.status_code == 200:
                data = response.json()
                final_summary = data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
                if final_summary:
                    return {"status": "success", "summary": final_summary}
                else:
                    return {"status": "error", "message": "API为Reduce阶段返回空内容"}
            else:
                error_msg = f"API错误 {response.status_code}: {_extract_api_error_message(response)}"
                # 对于客户端错误，不进行重试
                if response.status_code in [400, 401, 403, 429]:
                    return {"status": "error", "message": f"整合摘要失败 ({error_msg})"}
        except requests.exceptions.Timeout:
            error_msg = "请求超时"
        except requests.exceptions.RequestException as e:
            error_msg = f"网络连接错误: {type(e).__name__}"
        except Exception as e:
            error_msg = f"未知错误: {str(e)}"
        
        # 如果是最后一次重试，返回错误
        if attempt == RETRY_ATTEMPTS - 1:
            return {"status": "error", "message": f"处理Reduce阶段时发生错误: {error_msg}"}
        
        # 重试前等待，使用与其他函数一致的退避策略
        time.sleep(2 * (attempt + 1))
    
    return {"status": "error", "message": "未知错误，已达最大重试次数"}

# 笔记生成核心处理函数
def _perform_notes_generation(text_to_process):
    '''
    生成笔记功能
    - 直接对整个文本进行单次API调用（不使用Map-Reduce）
    - 将文本包裹在<待处理文本>标签内
    - 使用PROMPT_GENERATE_NOTES作为系统prompt
    - 设置temperature=0.2确保学术准确性
    - 包含重试机制
    '''
    if not text_to_process or not text_to_process.strip():
        return {"status": "error", "message": "待处理文本不能为空"}
    
    # 检查API配置
    opt_configured_properly = OPT_API_KEY and OPT_API_URL and OPT_API_URL.startswith(('http://', 'https://')) and NOTES_MODEL
    if not opt_configured_properly:
        skip_reason_parts = []
        if not OPT_API_KEY: skip_reason_parts.append("缺少API Key")
        if not OPT_API_URL or not OPT_API_URL.startswith(('http://', 'https://')): skip_reason_parts.append("API URL无效")
        if not NOTES_MODEL: skip_reason_parts.append("缺少笔记生成模型名称")
        skip_reason = ", ".join(skip_reason_parts)
        return {"status": "error", "message": f"笔记生成服务配置不完整: {skip_reason}"}
    
    print(f"开始生成笔记，文本长度: {len(text_to_process)} 字符")
    
    # 将文本包裹在待处理文本标签内
    wrapped_text = f"<待处理文本>\n{text_to_process.strip()}\n</待处理文本>"
    
    # 构建API请求
    messages = [
        {"role": "system", "content": PROMPT_GENERATE_NOTES},
        {"role": "user", "content": wrapped_text}
    ]
    payload = {
        'model': NOTES_MODEL,
        'messages': messages,
        'temperature': 0.2  # 确保学术准确性
    }
    headers = {
        'Authorization': f'Bearer {OPT_API_KEY}',
        'Content-Type': 'application/json'
    }
    
    # 重试机制
    for attempt in range(RETRY_ATTEMPTS):
        try:
            print(f"笔记生成API调用 (尝试 {attempt + 1}/{RETRY_ATTEMPTS})")
            response = requests.post(OPT_API_URL, headers=headers, json=payload, timeout=300)
            
            if response.status_code == 200:
                data = response.json()
                content = data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
                if content:
                    print("笔记生成成功")
                    return {"status": "success", "notes": content}
                else:
                    return {"status": "error", "message": "API返回空内容"}
            else:
                error_msg = f"API错误 {response.status_code}: {_extract_api_error_message(response)}"
                # 对于客户端错误，不进行重试
                if response.status_code in [400, 401, 403, 429]:
                    return {"status": "error", "message": error_msg}
                
        except requests.exceptions.Timeout:
            error_msg = "请求超时"
        except requests.exceptions.RequestException as e:
            error_msg = f"网络连接错误: {type(e).__name__}"
        except Exception as e:
            error_msg = f"未知错误: {str(e)}"
        
        # 如果是最后一次重试，返回错误
        if attempt == RETRY_ATTEMPTS - 1:
            print(f"笔记生成失败: {error_msg}")
            return {"status": "error", "message": error_msg}
        
        # 重试前等待
        wait_time = 2 * (attempt + 1)
        print(f"笔记生成失败，{wait_time}秒后重试: {error_msg}")
        time.sleep(wait_time)
    
    return {"status": "error", "message": "未知错误，已达最大重试次数"}

# =============================================================
# --- Web UI 页面服务路由 ---
# =============================================================
@app.route('/')
def index():
    return render_template('index.html')

# =============================================================
# --- OpenAI 兼容 API 路由 ---
# =============================================================
@app.before_request
def check_openai_auth():
    if request.path.startswith('/v1/'):
        if not API_ACCESS_TOKEN:
            print("警告: API被调用，但环境变量 API_ACCESS_TOKEN 未设置。拒绝访问。")
            return jsonify({"error": {"message": "API access is not configured on the server.", "type": "server_error"}}), 500
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"error": {"message": "Authorization header is missing or invalid.", "type": "invalid_request_error"}}), 401
        token = auth_header.split(' ')[1]
        if token != API_ACCESS_TOKEN:
            return jsonify({"error": {"message": "Incorrect API key provided.", "type": "invalid_request_error"}}), 401

@app.route('/v1/models', methods=['GET'])
def list_models():
    return jsonify({
        "object": "list",
        "data": [
            { "id": MODEL_CALIBRATE, "object": "model", "created": int(time.time()), "owned_by": "xy2yp" },
            { "id": MODEL_SUMMARIZE, "object": "model", "created": int(time.time()), "owned_by": "xy2yp" },
        ]
    })

@app.route('/v1/audio/transcriptions', methods=['POST'])
def openai_audio_transcriptions():
    if 'file' not in request.files: return jsonify({"error": "No file part in the request"}), 400
    audio_file = request.files['file']
    model_requested = request.form.get('model')
    if not model_requested or model_requested not in [MODEL_CALIBRATE, MODEL_SUMMARIZE]:
        return jsonify({"error": f"Model '{model_requested}' is not supported. Please use '{MODEL_CALIBRATE}' or '{MODEL_SUMMARIZE}'."}), 400

    def generate_response():
        try:
            print(f"API Call: Received request for model '{model_requested}'. Starting S2T...")
            yield " " 
            s2t_files = {'file': (audio_file.filename, audio_file.stream, audio_file.mimetype)}
            s2t_payload = {'model': S2T_MODEL}
            s2t_headers = {'Authorization': f'Bearer {S2T_API_KEY}'}
            s2t_response = requests.post(S2T_API_URL, files=s2t_files, data=s2t_payload, headers=s2t_headers, timeout=300)
            if s2t_response.status_code != 200:
                error_details = _extract_api_error_message(s2t_response)
                raise Exception(f"Upstream S2T service failed with status {s2t_response.status_code}: {error_details}")
            raw_transcription = s2t_response.json().get('text', '').strip()
            if not raw_transcription: raise Exception("Upstream S2T service returned empty text.")
        except Exception as e:
            error_payload = {"error": {"message": str(e), "type": "upstream_error", "code": "s2t_failed"}}
            yield json.dumps(error_payload, ensure_ascii=False)
            return

        print("API Call: S2T completed. Starting text optimization...")
        yield " " 
        calibrated_text, opt_message, is_calibrated = _perform_text_optimization(raw_transcription)
        
        final_response = {}
        if model_requested == MODEL_CALIBRATE:
            final_response["text"] = calibrated_text
            if not is_calibrated:
                final_response["x_warning"] = {"code": "calibration_failed", "message": f"Text optimization failed. Returning raw transcription. Reason: {opt_message}"}
        
        elif model_requested == MODEL_SUMMARIZE:
            if not is_calibrated:
                final_response["text"] = raw_transcription
                final_response["x_warning"] = {"code": "calibration_failed_in_summary_workflow", "message": f"The calibration step failed. Returning the raw, un-calibrated transcription as a fallback. Reason: {opt_message}"}
            else:
                yield " " 
                summary_result = _perform_summarization(calibrated_text)
                if summary_result['status'] == 'success':
                    final_response["text"] = summary_result['summary']
                else:
                    final_response["text"] = calibrated_text
                    final_response["x_warning"] = {"code": "summarization_failed", "message": f"Final summarization step failed. Returning the full calibrated text instead. Reason: {summary_result['message']}"}
        yield json.dumps(final_response, ensure_ascii=False)

    return Response(stream_with_context(generate_response()), mimetype='application/json; charset=utf-8')


# =============================================================
# ---  Web UI 数据接口路由API ---
# =============================================================
@app.route('/api/transcribe', methods=['POST'])
def transcribe_and_optimize_audio():
    print("\n--- [Transcribe] 请求开始 ---")
    audio_file = request.files.get('audio_file')
    if not audio_file:
        print("[Transcribe] 错误: 请求中缺少音频文件。")
        return jsonify({"error": "缺少上传的音频文件"}), 400
    
    try:
        s2t_files = {'file': (audio_file.filename, audio_file.stream, audio_file.mimetype)}
        s2t_payload = {'model': S2T_MODEL}
        s2t_headers = {'Authorization': f'Bearer {S2T_API_KEY}'}
        
        print(f"[Transcribe] 正在调用 S2T API: {S2T_API_URL}")
        start_time = time.time()
        s2t_response = requests.post(S2T_API_URL, files=s2t_files, data=s2t_payload, headers=s2t_headers, timeout=300)
        end_time = time.time()
        print(f"[Transcribe] S2T API 响应完毕. 状态码: {s2t_response.status_code}, 耗时: {end_time - start_time:.2f} 秒.")

        if s2t_response.status_code != 200:
            error_details = _extract_api_error_message(s2t_response)
            print(f"[Transcribe] S2T API 错误: {s2t_response.status_code} - {error_details}")
            return jsonify({"error": f"S2T API 返回错误: {s2t_response.status_code} - {error_details}"}), 500
        
        raw_transcription = s2t_response.json().get('text', '').strip()
        if not raw_transcription:
            print("[Transcribe] 错误: S2T 服务未能识别出任何文本。")
            return jsonify({"error": "S2T 服务未能识别出任何文本。"}), 500
        print("[Transcribe] S2T 文本获取成功.")

    except requests.exceptions.Timeout:
        print(f"[Transcribe] 错误: 调用 S2T API 超时 (超过300秒).")
        return jsonify({"error": "调用 S2T API 超时"}), 500
    except Exception as e:
        print(f"[Transcribe] 错误: 处理 S2T 请求时发生未知错误: {type(e).__name__} - {e}")
        return jsonify({"error": f"处理 S2T 请求时发生未知错误: {type(e).__name__}"}), 500

    print("[Transcribe] 正在调用文本优化...")
    final_transcription, opt_message, is_calibrated = _perform_text_optimization(raw_transcription)
    print(f"[Transcribe] 文本优化完成. 状态: {opt_message}")
    
    if is_calibrated:
        final_status_message = f"转录完成，{opt_message}"
    elif "跳过" in opt_message:
        final_status_message = f"转录完成 ({opt_message.replace('校准已跳过', '校准服务')})"
    else:
        final_status_message = f"转录完成，{opt_message.replace('校准失败', '但校准失败')}"
    
    print("[Transcribe] 请求处理完毕，正在返回结果。")
    return jsonify({"status": "success", "transcription": final_transcription, "raw_transcription": raw_transcription, "calibration_message": final_status_message, "is_calibrated": is_calibrated})

@app.route('/api/recalibrate', methods=['POST'])
def recalibrate_text():
    data = request.get_json()
    if not data or 'raw_transcription' not in data: return jsonify({"error": "请求体无效或缺少 raw_transcription 字段"}), 400
    raw_text = data.get('raw_transcription')
    if not isinstance(raw_text, str) or not raw_text.strip(): return jsonify({"error": "需要重新校准的文本不能为空"}), 400
    calibrated_text, calibration_status_msg, calibration_success = _perform_text_optimization(raw_text)
    return jsonify({"status": "success", "transcription": calibrated_text, "calibration_message": calibration_status_msg, "is_calibrated": calibration_success})

@app.route('/api/summarize', methods=['POST'])
def summarize_text():
    data = request.get_json()
    if not data or 'text_to_summarize' not in data: return jsonify({"error": "请求体无效或缺少 'text_to_summarize' 字段"}), 400
    text = data.get('text_to_summarize')
    if not text or not text.strip(): return jsonify({"error": "待总结的文本不能为空"}), 400
    result = _perform_summarization(text)
    if result['status'] == 'success': return jsonify({"summary": result['summary']})
    else: return jsonify({"error": result['message']}), 500

@app.route('/api/generatenote', methods=['POST'])
def generate_notes():
    data = request.get_json()
    if not data or 'text_to_process' not in data:
        return jsonify({"error": "请求体无效或缺少 'text_to_process' 字段"}), 400
    
    text = data.get('text_to_process')
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "待处理的文本不能为空"}), 400
    
    print(f"收到笔记生成请求，文本长度: {len(text)} 字符")
    
    result = _perform_notes_generation(text)
    if result['status'] == 'success':
        return jsonify({"status": "success", "notes": result['notes']})
    else:
        return jsonify({"error": result['message']}), 500

# --- 主程序启动入口 ---
if __name__ == '__main__':
    # 启动时进行配置检查
    print("--- S2T 配置检查 ---")
    if not S2T_API_KEY: print("警告: 环境变量 S2T_API_KEY 未设置。")
    if not S2T_API_URL.startswith(('http://', 'https://')): print(f"警告: 环境变量 S2T_API_URL 格式不正确: {S2T_API_URL}。")
    
    print("\n--- OPT 配置检查 ---")
    opt_configured_for_check = OPT_API_KEY or (OPT_API_URL and OPT_API_URL != 'https://api.openai.com/v1/chat/completions') or OPT_MODEL
    if opt_configured_for_check:
        if not OPT_API_KEY: print("警告: 环境变量 OPT_API_KEY 未设置。文本优化功能将无法使用。")
        if OPT_API_KEY and (not OPT_API_URL or not OPT_API_URL.startswith(('http://', 'https://'))): print(f"警告: 环境变量 OPT_API_URL ({OPT_API_URL}) 无效或格式不正确。")
        if OPT_API_KEY and not OPT_MODEL: print("警告: 已设置 OPT_API_KEY 但未设置 OPT_MODEL。")
        
        # 检查各功能的专用模型配置
        print("\n--- 功能专用模型配置检查 ---")
        calibration_custom = os.environ.get('CALIBRATION_MODEL') is not None
        summary_custom = os.environ.get('SUMMARY_MODEL') is not None
        notes_custom = os.environ.get('NOTES_MODEL') is not None
        
        if calibration_custom:
            print(f"✓ 校准功能使用专用模型: {CALIBRATION_MODEL}")
        else:
            print(f"✓ 校准功能使用默认模型: {CALIBRATION_MODEL} (来自OPT_MODEL)")
            
        if summary_custom:
            print(f"✓ 摘要功能使用专用模型: {SUMMARY_MODEL}")
        else:
            print(f"✓ 摘要功能使用默认模型: {SUMMARY_MODEL} (来自OPT_MODEL)")
            
        if notes_custom:
            print(f"✓ 笔记功能使用专用模型: {NOTES_MODEL}")
        else:
            print(f"✓ 笔记功能使用默认模型: {NOTES_MODEL} (来自OPT_MODEL)")
    else:
        print("提示: OPT服务未配置，校准、总结和笔记生成功能将不可用。")

    print("\n--- API 封装功能检查 ---")
    if not API_ACCESS_TOKEN:
        print("警告: 环境变量 API_ACCESS_TOKEN 未设置或为空。API封装功能将无法通过认证。")
    else:
        print("API封装功能已启用。")
    
    print("\n--------------------\n")
    print(f"服务器正在启动，监听 http://0.0.0.0:6701")
    serve(app, host='0.0.0.0', port=6701)
