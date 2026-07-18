import os
import json
import copy
import math
import random
import re
from .utils import *
import os
from concurrent.futures import ThreadPoolExecutor, as_completed


################### check title in page #########################################################
async def check_title_appearance(item, page_list, start_index=1, model=None):    
    title=item['title']
    if 'physical_index' not in item or item['physical_index'] is None:
        return {'list_index': item.get('list_index'), 'answer': 'no', 'title':title, 'page_number': None}
    
    
    page_number = item['physical_index']
    page_text = page_list[page_number-start_index][0]

    
    prompt = f"""
    Your job is to check if the given section appears or starts in the given page_text.

    Note: do fuzzy matching, ignore any space inconsistency in the page_text.

    The given section title is {title}.
    The given page_text is {page_text}.
    
    Reply format:
    {{
        
        "thinking": <why do you think the section appears or starts in the page_text>
        "answer": "yes or no" (yes if the section appears or starts in the page_text, no otherwise)
    }}
    Directly return the final JSON structure. Do not output anything else."""

    response = await llm_acompletion(model=model, prompt=prompt)
    response = extract_json(response)
    if 'answer' in response:
        answer = response['answer']
    else:
        answer = 'no'
    return {'list_index': item['list_index'], 'answer': answer, 'title': title, 'page_number': page_number}


async def check_title_appearance_in_start(title, page_text, model=None, logger=None):    
    prompt = f"""
    You will be given the current section title and the current page_text.
    Your job is to check if the current section starts in the beginning of the given page_text.
    Ignore page headers, footers, page numbers, copyright lines, "Page X of Y" markers,
    and any running header/footer text.
    Section headings, sub-headings, and subsection titles ARE main body content — do NOT ignore them.
    If there are other main body contents (including other section headings) before the current
    section title, then the current section does not start in the beginning of the given page_text.
    If the current section title is the first main body content in the given page_text, then the
    current section starts in the beginning of the given page_text.

    Note: do fuzzy matching, ignore any space inconsistency in the page_text.

    The given section title is {title}.
    The given page_text is {page_text}.
    
    reply format:
    {{
        "thinking": <why do you think the section appears or starts in the page_text>
        "start_begin": "yes or no" (yes if the section starts in the beginning of the page_text, no otherwise)
    }}
    Directly return the final JSON structure. Do not output anything else."""

    response = await llm_acompletion(model=model, prompt=prompt)
    response = extract_json(response)
    if logger:
        logger.info(f"Response: {response}")
    return response.get("start_begin", "no")


async def check_title_appearance_in_start_concurrent(structure, page_list, model=None, logger=None):
    if logger:
        logger.info("Checking title appearance in start concurrently")

    def _valid_physical_index(item):
        idx = item.get('physical_index')
        return idx is not None and 1 <= idx <= len(page_list)

    for item in structure:
        if not _valid_physical_index(item):
            item['appear_start'] = 'no'

    tasks = []
    valid_items = []
    for item in structure:
        if _valid_physical_index(item):
            page_text = page_list[item['physical_index'] - 1][0]
            struct = item.get('structure', '')
            full_title = f"{struct} {item['title']}" if struct and not struct.startswith('_') else item['title']
            tasks.append(check_title_appearance_in_start(full_title, page_text, model=model, logger=logger))
            valid_items.append(item)

    total = len(tasks)
    print(f"  Checking title positions for {total} sections...")
    completed = [0]
    progress_fmt = f"  Title check: {{}}/{total} ({{:3d}}%)"

    async def _tracked(task):
        try:
            return await task
        finally:
            completed[0] += 1
            pct = 100 * completed[0] // total
            print("\r" + progress_fmt.format(completed[0], pct), end="", flush=True)

    tracked_tasks = [_tracked(t) for t in tasks]
    results = await asyncio.gather(*tracked_tasks, return_exceptions=True)
    print()
    for item, result in zip(valid_items, results):
        if isinstance(result, Exception):
            if logger:
                logger.error(f"Error checking start for {item['title']}: {result}")
            item['appear_start'] = 'no'
        else:
            item['appear_start'] = result

    return structure


def toc_detector_single_page(content, model=None):
    prompt = f"""
    Your job is to detect if there is a table of content provided in the given text.

    Given text: {content}

    return the following JSON format:
    {{
        "thinking": <why do you think there is a table of content in the given text>
        "toc_detected": "<yes or no>",
    }}

    Directly return the final JSON structure. Do not output anything else.
    Please note: abstract,summary, notation list, figure list, table list, etc. are not table of contents."""

    response = llm_completion(model=model, prompt=prompt)
    json_content = extract_json(response)    
    return json_content.get('toc_detected', 'no')


def check_if_toc_extraction_is_complete(content, toc, model=None):
    prompt = f"""
    You are given a partial document  and a  table of contents.
    Your job is to check if the  table of contents is complete, which it contains all the main sections in the partial document.

    Reply format:
    {{
        "thinking": <why do you think the table of contents is complete or not>
        "completed": "yes" or "no"
    }}
    Directly return the final JSON structure. Do not output anything else."""

    prompt = prompt + '\n Document:\n' + content + '\n Table of contents:\n' + toc
    response = llm_completion(model=model, prompt=prompt)
    json_content = extract_json(response)
    return json_content.get('completed', 'no')


def check_if_toc_transformation_is_complete(content, toc, model=None):
    prompt = f"""
    You are given a raw table of contents and a  table of contents.
    Your job is to check if the  table of contents is complete.

    Reply format:
    {{
        "thinking": <why do you think the cleaned table of contents is complete or not>
        "completed": "yes" or "no"
    }}
    Directly return the final JSON structure. Do not output anything else."""

    prompt = prompt + '\n Raw Table of contents:\n' + content + '\n Cleaned Table of contents:\n' + toc
    response = llm_completion(model=model, prompt=prompt)
    json_content = extract_json(response)
    return json_content.get('completed', 'no')

def extract_toc_content(content, model=None):
    prompt = f"""
    Your job is to extract the full table of contents from the given text, replace ... with :

    Given text: {content}

    Directly return the full table of contents content. Do not output anything else."""

    response, finish_reason = llm_completion(model=model, prompt=prompt, return_finish_reason=True)
    
    if_complete = check_if_toc_transformation_is_complete(content, response, model)
    if if_complete == "yes" and finish_reason == "finished":
        return response
    
    chat_history = [
        {"role": "user", "content": prompt}, 
        {"role": "assistant", "content": response},    
    ]
    continue_prompt = "please continue the generation of table of contents, directly output the remaining part of the structure"
    
    max_attempts = 5
    for attempt in range(max_attempts):
        new_response, finish_reason = llm_completion(model=model, prompt=continue_prompt, chat_history=chat_history, return_finish_reason=True)
        response = response + new_response
        chat_history.append({"role": "user", "content": continue_prompt})
        chat_history.append({"role": "assistant", "content": new_response})
        if_complete = check_if_toc_transformation_is_complete(content, response, model)
        if if_complete == "yes" and finish_reason == "finished":
            break
    else:
        raise Exception('Failed to complete table of contents extraction after maximum retries')
    
    return response

def detect_page_index(toc_content, model=None):
    print('start detect_page_index')
    prompt = f"""
    You will be given a table of contents.

    Your job is to detect if there are page numbers/indices given within the table of contents.

    Given text: {toc_content}

    Reply format:
    {{
        "thinking": <why do you think there are page numbers/indices given within the table of contents>
        "page_index_given_in_toc": "<yes or no>"
    }}
    Directly return the final JSON structure. Do not output anything else."""

    response = llm_completion(model=model, prompt=prompt)
    json_content = extract_json(response)
    return json_content.get('page_index_given_in_toc', 'no')

def toc_extractor(page_list, toc_page_list, model):
    def transform_dots_to_colon(text):
        text = re.sub(r'\.{5,}', ': ', text)
        # Handle dots separated by spaces
        text = re.sub(r'(?:\. ){5,}\.?', ': ', text)
        return text
    
    toc_content = ""
    for page_index in toc_page_list:
        toc_content += page_list[page_index][0]
    toc_content = transform_dots_to_colon(toc_content)
    has_page_index = detect_page_index(toc_content, model=model)
    
    return {
        "toc_content": toc_content,
        "page_index_given_in_toc": has_page_index
    }




def toc_index_extractor(toc, content, model=None):
    print('start toc_index_extractor')
    toc_extractor_prompt = """
    You are given a table of contents in a json format and several pages of a document, your job is to add the physical_index to the table of contents in the json format.

    The provided pages contains tags like <physical_index_X> and <physical_index_X> to indicate the physical location of the page X.

    The structure variable is the numeric system which represents the index of the hierarchy section in the table of contents. For example, the first section has structure index 1, the first subsection has structure index 1.1, the second subsection has structure index 1.2, etc.

    The response should be in the following JSON format: 
    [
        {
            "structure": <structure index, "x.x.x" or None> (string),
            "title": <title of the section>,
            "physical_index": "<physical_index_X>" (keep the format)
        },
        ...
    ]

    Only add the physical_index to the sections that are in the provided pages.
    If the section is not in the provided pages, do not add the physical_index to it.
    Directly return the final JSON structure. Do not output anything else."""

    prompt = toc_extractor_prompt + '\nTable of contents:\n' + str(toc) + '\nDocument pages:\n' + content
    response = llm_completion(model=model, prompt=prompt)
    json_content = extract_json(response)    
    return json_content



def toc_transformer_from_pdf_toc(pdf_path):
    """Bypass LLM-based TOC transformation when the PDF already has structured bookmarks.

    PyMuPDF's get_toc() returns entries shaped [level, title, page, ...]. We map them to
    the same [{structure, title, page}, ...] format that toc_transformer() produces, so
    downstream callers (process_toc_no_page_numbers, process_toc_with_page_numbers) keep
    working unchanged.

    USB4.pdf ships with 1000+ bookmarks covering the full hierarchy, so this short-circuit
    is both faster and more reliable than asking an LLM to re-structure the raw TOC text.
    """
    print('start toc_transformer_from_pdf_toc (PyMuPDF bookmarks, no LLM call)')
    progress_log('toc_transformer_from_pdf_toc: opening PDF bookmarks')
    import fitz  # PyMuPDF, already a runtime dep via pymupdf
    import re as _re

    doc = fitz.open(pdf_path)
    raw_toc = doc.get_toc(simple=False)

    if not raw_toc:
        raise RuntimeError(
            f'PDF has no bookmark TOC: {pdf_path}. '
            'toc_transformer_from_pdf_toc only works when PyMuPDF can extract a structured TOC; '
            'fall back to the LLM-based toc_transformer() for scan-only PDFs.'
        )

    # USB4 (and most technical specs) embed the chapter number at the start of the title,
    # e.g. "1 Introduction", "1.1 Scope", "1.6.2.7 Shall", "F Gen 4 Datapath". Extracting
    # that prefix is more reliable than reconstructing it from PyMuPDF's integer levels,
    # because the integer level only reflects bookmark nesting — not the actual numbering
    # scheme the document uses.
    structure_re = re.compile(r'^\s*((?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*))\s+')

    # Per-level counter for unnumbered headings that fall back to PyMuPDF level.
    # Without unique keys, "Purpose"/"Summary"/"Contents" (all level 1, no number)
    # would all get structure='1', and list_to_tree would silently overwrite them.
    # We also track the last structure at each level so that fallback children (level>1)
    # inherit their parent's prefix — otherwise list_to_tree's get_parent_structure
    # (which splits on '.') can't find the parent and promotes them to root nodes.
    fallback_counters = {}       # {level: count}
    last_at_level = {}           # {level: last structure seen at this level}

    result = []
    for entry in raw_toc:
        level, title, page = entry[0], entry[1], entry[2]
        # PyMuPDF's get_toc(simple=False) returns 1-based page numbers.
        # We use them directly — the rest of PageIndex expects 1-indexed physical pages.
        physical_page = page

        m = structure_re.match(title)
        if m:
            structure = m.group(1)
            clean_title = title[m.end():].strip()
        else:
            # No numeric prefix found (e.g. unnumbered front-matter heading).
            count = fallback_counters.get(level, 0) + 1
            fallback_counters[level] = count
            if level == 1:
                structure = f'_{count}'
            else:
                parent = last_at_level.get(level - 1, '')
                structure = f'{parent}.{count}' if parent else f'{count}'
            clean_title = title.strip()

        last_at_level[level] = structure
        # Clear deeper levels (bookmark nesting may skip back up)
        for l in list(last_at_level.keys()):
            if l > level:
                del last_at_level[l]

        result.append({
            'structure': structure,
            'title': clean_title,
            'page': physical_page,
            # Marker so downstream (meta_processor) can identify this TOC as authoritative
            # and skip the slow verify_toc LLM call.
            'from_pdf_toc': True,
        })

    progress_log(f'toc_transformer_from_pdf_toc: extracted {len(result)} entries, {len(fallback_counters)} levels with fallback')
    print(f'toc_transformer_from_pdf_toc: extracted {len(result)} entries from PyMuPDF bookmarks')
    return result


def toc_transformer(toc_content, model=None):
    print('start toc_transformer')
    init_prompt = """
    You are given a table of contents, You job is to transform the whole table of content into a JSON format included table_of_contents.

    structure is the numeric system which represents the index of the hierarchy section in the table of contents. For example, the first section has structure index 1, the first subsection has structure index 1.1, the second subsection has structure index 1.2, etc.

    The response should be in the following JSON format: 
    {
    table_of_contents: [
        {
            "structure": <structure index, "x.x.x" or None> (string),
            "title": <title of the section>,
            "page": <page number or None>,
        },
        ...
        ],
    }
    You should transform the full table of contents in one go.
    Directly return the final JSON structure, do not output anything else. """

    prompt = init_prompt + '\n Given table of contents\n:' + toc_content
    last_complete, finish_reason = llm_completion(model=model, prompt=prompt, return_finish_reason=True)
    if_complete = check_if_toc_transformation_is_complete(toc_content, last_complete, model)
    if if_complete == "yes" and finish_reason == "finished":
        last_complete = extract_json(last_complete)
        cleaned_response = convert_page_to_int(last_complete.get('table_of_contents', []))
        return cleaned_response
    
    last_complete = get_json_content(last_complete)
    chat_history = [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": last_complete},
    ]
    continue_prompt = "Please continue the table of contents JSON structure from where you left off. Directly output only the remaining part."

    position = last_complete.rfind('}')
    if position != -1:
        last_complete = last_complete[:position+2]

    max_attempts = 5
    for attempt in range(max_attempts):

        new_complete, finish_reason = llm_completion(model=model, prompt=continue_prompt, chat_history=chat_history, return_finish_reason=True)

        if new_complete.startswith('```json'):
            new_complete = get_json_content(new_complete)
        last_complete = last_complete + new_complete

        chat_history.append({"role": "user", "content": continue_prompt})
        chat_history.append({"role": "assistant", "content": new_complete})

        if_complete = check_if_toc_transformation_is_complete(toc_content, last_complete, model)
        if if_complete == "yes" and finish_reason == "finished":
            break
    else:
        raise Exception('Failed to complete TOC transformation after maximum retries')

    last_complete = extract_json(last_complete)

    cleaned_response = convert_page_to_int(last_complete.get('table_of_contents', []))
    return cleaned_response
    



def find_toc_pages(start_page_index, page_list, opt, logger=None):
    print('start find_toc_pages')
    last_page_is_yes = False
    toc_page_list = []
    i = start_page_index
    
    while i < len(page_list):
        # Only check beyond max_pages if we're still finding TOC pages
        if i >= opt.toc_check_page_num and not last_page_is_yes:
            break
        detected_result = toc_detector_single_page(page_list[i][0],model=opt.model)
        if detected_result == 'yes':
            if logger:
                logger.info(f'Page {i} has toc')
            toc_page_list.append(i)
            last_page_is_yes = True
        elif detected_result == 'no' and last_page_is_yes:
            if logger:
                logger.info(f'Found the last page with toc: {i-1}')
            break
        i += 1
    
    if not toc_page_list and logger:
        logger.info('No toc found')
        
    return toc_page_list

def remove_page_number(data):
    if isinstance(data, dict):
        data.pop('page_number', None)  
        for key in list(data.keys()):
            if 'nodes' in key:
                remove_page_number(data[key])
    elif isinstance(data, list):
        for item in data:
            remove_page_number(item)
    return data

def extract_matching_page_pairs(toc_page, toc_physical_index, start_page_index):
    pairs = []
    for phy_item in toc_physical_index:
        for page_item in toc_page:
            if phy_item.get('title') == page_item.get('title'):
                physical_index = phy_item.get('physical_index')
                if physical_index is not None and int(physical_index) >= start_page_index:
                    pairs.append({
                        'title': phy_item.get('title'),
                        'page': page_item.get('page'),
                        'physical_index': physical_index
                    })
    return pairs


def calculate_page_offset(pairs):
    differences = []
    for pair in pairs:
        try:
            physical_index = pair['physical_index']
            page_number = pair['page']
            difference = physical_index - page_number
            differences.append(difference)
        except (KeyError, TypeError):
            continue
    
    if not differences:
        return None
    
    difference_counts = {}
    for diff in differences:
        difference_counts[diff] = difference_counts.get(diff, 0) + 1
    
    most_common = max(difference_counts.items(), key=lambda x: x[1])[0]
    
    return most_common

def add_page_offset_to_toc_json(data, offset):
    for i in range(len(data)):
        if data[i].get('page') is not None and isinstance(data[i]['page'], int):
            data[i]['physical_index'] = data[i]['page'] + offset
            del data[i]['page']
    
    return data



def page_list_to_group_text(page_contents, token_lengths, max_tokens=20000, overlap_page=1):    
    num_tokens = sum(token_lengths)
    
    if num_tokens <= max_tokens:
        # merge all pages into one text
        page_text = "".join(page_contents)
        return [page_text]
    
    subsets = []
    current_subset = []
    current_token_count = 0

    expected_parts_num = math.ceil(num_tokens / max_tokens)
    average_tokens_per_part = math.ceil(((num_tokens / expected_parts_num) + max_tokens) / 2)
    
    for i, (page_content, page_tokens) in enumerate(zip(page_contents, token_lengths)):
        if current_token_count + page_tokens > average_tokens_per_part:

            subsets.append(''.join(current_subset))
            # Start new subset from overlap if specified
            overlap_start = max(i - overlap_page, 0)
            current_subset = page_contents[overlap_start:i]
            current_token_count = sum(token_lengths[overlap_start:i])
        
        # Add current page to the subset
        current_subset.append(page_content)
        current_token_count += page_tokens

    # Add the last subset if it contains any pages
    if current_subset:
        subsets.append(''.join(current_subset))
    
    print('divide page_list to groups', len(subsets))
    return subsets

def add_page_number_to_toc(part, structure, model=None):
    fill_prompt_seq = """
    You are given an JSON structure of a document and a partial part of the document. Your task is to check if the title that is described in the structure is started in the partial given document.

    The provided text contains tags like <physical_index_X> and <physical_index_X> to indicate the physical location of the page X. 

    If the full target section starts in the partial given document, insert the given JSON structure with the "start": "yes", and "start_index": "<physical_index_X>".

    If the full target section does not start in the partial given document, insert "start": "no",  "start_index": None.

    The response should be in the following format. 
        [
            {
                "structure": <structure index, "x.x.x" or None> (string),
                "title": <title of the section>,
                "start": "<yes or no>",
                "physical_index": "<physical_index_X> (keep the format)" or None
            },
            ...
        ]    
    The given structure contains the result of the previous part, you need to fill the result of the current part, do not change the previous result.
    Directly return the final JSON structure. Do not output anything else."""

    prompt = fill_prompt_seq + f"\n\nCurrent Partial Document:\n{part}\n\nGiven Structure\n{json.dumps(structure, indent=2)}\n"
    current_json_raw = llm_completion(model=model, prompt=prompt)
    json_result = extract_json(current_json_raw)
    
    for item in json_result:
        if 'start' in item:
            del item['start']
    return json_result


def remove_first_physical_index_section(text):
    """
    Removes the first section between <physical_index_X> and <physical_index_X> tags,
    and returns the remaining text.
    """
    pattern = r'<physical_index_\d+>.*?<physical_index_\d+>'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        # Remove the first matched section
        return text.replace(match.group(0), '', 1)
    return text

### add verify completeness
def generate_toc_continue(toc_content, part, model=None):
    print('start generate_toc_continue')
    prompt = """
    You are an expert in extracting hierarchical tree structure.
    You are given a tree structure of the previous part and the text of the current part.
    Your task is to continue the tree structure from the previous part to include the current part.

    The structure variable is the numeric system which represents the index of the hierarchy section in the table of contents. For example, the first section has structure index 1, the first subsection has structure index 1.1, the second subsection has structure index 1.2, etc.

    For the title, you need to extract the original title from the text, only fix the space inconsistency.

    The provided text contains tags like <physical_index_X> and <physical_index_X> to indicate the start and end of page X. \
    
    For the physical_index, you need to extract the physical index of the start of the section from the text. Keep the <physical_index_X> format.

    The response should be in the following format. 
        [
            {
                "structure": <structure index, "x.x.x"> (string),
                "title": <title of the section, keep the original title>,
                "physical_index": "<physical_index_X> (keep the format)"
            },
            ...
        ]    

    Directly return the additional part of the final JSON structure. Do not output anything else."""

    prompt = prompt + '\nGiven text\n:' + part + '\nPrevious tree structure\n:' + json.dumps(toc_content, indent=2)
    response, finish_reason = llm_completion(model=model, prompt=prompt, return_finish_reason=True)
    if finish_reason == 'finished':
        return extract_json(response)
    else:
        raise Exception(f'finish reason: {finish_reason}')
    
### add verify completeness
def generate_toc_init(part, model=None):
    print('start generate_toc_init')
    prompt = """
    You are an expert in extracting hierarchical tree structure, your task is to generate the tree structure of the document.

    The structure variable is the numeric system which represents the index of the hierarchy section in the table of contents. For example, the first section has structure index 1, the first subsection has structure index 1.1, the second subsection has structure index 1.2, etc.

    For the title, you need to extract the original title from the text, only fix the space inconsistency.

    The provided text contains tags like <physical_index_X> and <physical_index_X> to indicate the start and end of page X. 

    For the physical_index, you need to extract the physical index of the start of the section from the text. Keep the <physical_index_X> format.

    The response should be in the following format. 
        [
            {{
                "structure": <structure index, "x.x.x"> (string),
                "title": <title of the section, keep the original title>,
                "physical_index": "<physical_index_X> (keep the format)"
            }},
            
        ],


    Directly return the final JSON structure. Do not output anything else."""

    prompt = prompt + '\nGiven text\n:' + part
    response, finish_reason = llm_completion(model=model, prompt=prompt, return_finish_reason=True)

    if finish_reason == 'finished':
         return extract_json(response)
    else:
        raise Exception(f'finish reason: {finish_reason}')

def process_no_toc(page_list, start_index=1, model=None, logger=None):
    page_contents=[]
    token_lengths=[]
    for page_index in range(start_index, start_index+len(page_list)):
        page_text = f"<physical_index_{page_index}>\n{page_list[page_index-start_index][0]}\n<physical_index_{page_index}>\n\n"
        page_contents.append(page_text)
        token_lengths.append(count_tokens(page_text, model))
    group_texts = page_list_to_group_text(page_contents, token_lengths)
    logger.info(f'len(group_texts): {len(group_texts)}')

    toc_with_page_number= generate_toc_init(group_texts[0], model)
    for group_text in group_texts[1:]:
        toc_with_page_number_additional = generate_toc_continue(toc_with_page_number, group_text, model)    
        toc_with_page_number.extend(toc_with_page_number_additional)
    logger.info(f'generate_toc: {toc_with_page_number}')

    toc_with_page_number = convert_physical_index_to_int(toc_with_page_number)
    logger.info(f'convert_physical_index_to_int: {toc_with_page_number}')

    return toc_with_page_number

def process_toc_no_page_numbers(toc_content, toc_page_list, page_list,  start_index=1, model=None, logger=None, doc=None):
    page_contents=[]
    token_lengths=[]
    # Short-circuit: if a PDF path is supplied and PyMuPDF can extract structured
    # bookmarks, use those directly instead of paying for an LLM re-structuring.
    # The PDF's bookmarks carry title + page + chapter number, so we already have
    # everything the rest of the pipeline needs.
    if doc and isinstance(doc, str) and doc.lower().endswith('.pdf'):
        try:
            toc_content = toc_transformer_from_pdf_toc(doc)
        except RuntimeError as e:
            if logger:
                logger.info(f'toc_transformer_from_pdf_toc unavailable, falling back to LLM: {e}')
            toc_content = toc_transformer(toc_content, model)
    else:
        toc_content = toc_transformer(toc_content, model)
    if logger:
        logger.info(f'toc_transformer: {toc_content}')
    for page_index in range(start_index, start_index+len(page_list)):
        page_text = f"<physical_index_{page_index}>\n{page_list[page_index-start_index][0]}\n<physical_index_{page_index}>\n\n"
        page_contents.append(page_text)
        token_lengths.append(count_tokens(page_text, model))
    
    group_texts = page_list_to_group_text(page_contents, token_lengths)
    logger.info(f'len(group_texts): {len(group_texts)}')

    toc_with_page_number=copy.deepcopy(toc_content)
    for group_text in group_texts:
        toc_with_page_number = add_page_number_to_toc(group_text, toc_with_page_number, model)
    logger.info(f'add_page_number_to_toc: {toc_with_page_number}')

    toc_with_page_number = convert_physical_index_to_int(toc_with_page_number)
    logger.info(f'convert_physical_index_to_int: {toc_with_page_number}')

    return toc_with_page_number



def process_toc_with_page_numbers(toc_content, toc_page_list, page_list, toc_check_page_num=None, model=None, logger=None, doc=None):
    # Short-circuit: if a PDF path is supplied and PyMuPDF can extract structured
    # bookmarks with page numbers, use those directly. PyMuPDF bookmarks are
    # authoritative — they were authored by the spec editor — so we can skip the
    # toc_index_extractor LLM call and the page-offset calculation that follows.
    if doc and isinstance(doc, str) and doc.lower().endswith('.pdf'):
        try:
            toc_with_page_number = toc_transformer_from_pdf_toc(doc)
            # The rest of the pipeline expects 'physical_index' (PyMuPDF bookmarks already
            # carry the physical page number, just under the 'page' key).
            toc_with_page_number = [
                {**item, 'physical_index': item['page']} if item.get('page') is not None else item
                for item in toc_with_page_number
            ]
            if logger:
                logger.info(f'toc_with_page_number (from PyMuPDF bookmarks): {len(toc_with_page_number)} entries')
            return toc_with_page_number
        except RuntimeError as e:
            if logger:
                logger.info(f'toc_transformer_from_pdf_toc unavailable, falling back to LLM: {e}')
            # If we got here via the PyMuPDF bookmark shortcut and toc_content is
            # empty (no real TOC text was extracted by check_toc), don't feed an
            # empty string to the LLM — it will hallucinate. Return empty instead.
            if not toc_content or not toc_content.strip():
                if logger:
                    logger.warning('PyMuPDF bookmark extraction failed with no TOC text fallback; returning empty TOC')
                return []

    toc_with_page_number = toc_transformer(toc_content, model)
    logger.info(f'toc_with_page_number: {toc_with_page_number}')

    toc_no_page_number = remove_page_number(copy.deepcopy(toc_with_page_number))
    
    start_page_index = toc_page_list[-1] + 1
    main_content = ""
    for page_index in range(start_page_index, min(start_page_index + toc_check_page_num, len(page_list))):
        main_content += f"<physical_index_{page_index+1}>\n{page_list[page_index][0]}\n<physical_index_{page_index+1}>\n\n"

    toc_with_physical_index = toc_index_extractor(toc_no_page_number, main_content, model)
    logger.info(f'toc_with_physical_index: {toc_with_physical_index}')

    toc_with_physical_index = convert_physical_index_to_int(toc_with_physical_index)
    logger.info(f'toc_with_physical_index: {toc_with_physical_index}')

    matching_pairs = extract_matching_page_pairs(toc_with_page_number, toc_with_physical_index, start_page_index)
    logger.info(f'matching_pairs: {matching_pairs}')

    offset = calculate_page_offset(matching_pairs)
    logger.info(f'offset: {offset}')

    toc_with_page_number = add_page_offset_to_toc_json(toc_with_page_number, offset)
    logger.info(f'toc_with_page_number: {toc_with_page_number}')

    toc_with_page_number = process_none_page_numbers(toc_with_page_number, page_list, model=model)
    logger.info(f'toc_with_page_number: {toc_with_page_number}')

    return toc_with_page_number



##check if needed to process none page numbers
def process_none_page_numbers(toc_items, page_list, start_index=1, model=None):
    for i, item in enumerate(toc_items):
        if "physical_index" not in item:
            # logger.info(f"fix item: {item}")
            # Find previous physical_index
            prev_physical_index = 0  # Default if no previous item exists
            for j in range(i - 1, -1, -1):
                if toc_items[j].get('physical_index') is not None:
                    prev_physical_index = toc_items[j]['physical_index']
                    break
            
            # Find next physical_index
            next_physical_index = -1  # Default if no next item exists
            for j in range(i + 1, len(toc_items)):
                if toc_items[j].get('physical_index') is not None:
                    next_physical_index = toc_items[j]['physical_index']
                    break

            page_contents = []
            for page_index in range(prev_physical_index, next_physical_index+1):
                # Add bounds checking to prevent IndexError
                list_index = page_index - start_index
                if list_index >= 0 and list_index < len(page_list):
                    page_text = f"<physical_index_{page_index}>\n{page_list[list_index][0]}\n<physical_index_{page_index}>\n\n"
                    page_contents.append(page_text)
                else:
                    continue

            item_copy = copy.deepcopy(item)
            item_copy.pop('page', None)
            result = add_page_number_to_toc(page_contents, item_copy, model)
            if isinstance(result[0]['physical_index'], str) and result[0]['physical_index'].startswith('<physical_index'):
                item['physical_index'] = int(result[0]['physical_index'].split('_')[-1].rstrip('>').strip())
                item.pop('page', None)
    
    return toc_items




def check_toc(page_list, opt=None):
    toc_page_list = find_toc_pages(start_page_index=0, page_list=page_list, opt=opt)
    if len(toc_page_list) == 0:
        print('no toc found')
        return {'toc_content': None, 'toc_page_list': [], 'page_index_given_in_toc': 'no'}
    else:
        print('toc found')
        toc_json = toc_extractor(page_list, toc_page_list, opt.model)

        if toc_json['page_index_given_in_toc'] == 'yes':
            print('index found')
            return {'toc_content': toc_json['toc_content'], 'toc_page_list': toc_page_list, 'page_index_given_in_toc': 'yes'}
        else:
            current_start_index = toc_page_list[-1] + 1
            
            while (toc_json['page_index_given_in_toc'] == 'no' and 
                   current_start_index < len(page_list) and 
                   current_start_index < opt.toc_check_page_num):
                
                additional_toc_pages = find_toc_pages(
                    start_page_index=current_start_index,
                    page_list=page_list,
                    opt=opt
                )
                
                if len(additional_toc_pages) == 0:
                    break

                additional_toc_json = toc_extractor(page_list, additional_toc_pages, opt.model)
                if additional_toc_json['page_index_given_in_toc'] == 'yes':
                    print('index found')
                    return {'toc_content': additional_toc_json['toc_content'], 'toc_page_list': additional_toc_pages, 'page_index_given_in_toc': 'yes'}

                else:
                    current_start_index = additional_toc_pages[-1] + 1
            print('index not found')
            return {'toc_content': toc_json['toc_content'], 'toc_page_list': toc_page_list, 'page_index_given_in_toc': 'no'}






################### fix incorrect toc #########################################################
async def single_toc_item_index_fixer(section_title, content, model=None):
    toc_extractor_prompt = """
    You are given a section title and several pages of a document, your job is to find the physical index of the start page of the section in the partial document.

    The provided pages contains tags like <physical_index_X> and <physical_index_X> to indicate the physical location of the page X.

    Reply in a JSON format:
    {
        "thinking": <explain which page, started and closed by <physical_index_X>, contains the start of this section>,
        "physical_index": "<physical_index_X>" (keep the format)
    }
    Directly return the final JSON structure. Do not output anything else."""

    prompt = toc_extractor_prompt + '\nSection Title:\n' + str(section_title) + '\nDocument pages:\n' + content
    response = await llm_acompletion(model=model, prompt=prompt)
    json_content = extract_json(response)
    physical_index = json_content.get('physical_index')
    if physical_index is None:
        return None
    return convert_physical_index_to_int(physical_index)



async def fix_incorrect_toc(toc_with_page_number, page_list, incorrect_results, start_index=1, model=None, logger=None):
    print(f'start fix_incorrect_toc with {len(incorrect_results)} incorrect results')
    incorrect_indices = {result['list_index'] for result in incorrect_results}
    
    end_index = len(page_list) + start_index - 1
    
    incorrect_results_and_range_logs = []
    # Helper function to process and check a single incorrect item
    async def process_and_check_item(incorrect_item):
        list_index = incorrect_item['list_index']
        
        # Check if list_index is valid
        if list_index < 0 or list_index >= len(toc_with_page_number):
            # Return an invalid result for out-of-bounds indices
            return {
                'list_index': list_index,
                'title': incorrect_item['title'],
                'physical_index': incorrect_item.get('physical_index'),
                'is_valid': False
            }
        
        # Find the previous correct item
        prev_correct = None
        for i in range(list_index-1, -1, -1):
            if i not in incorrect_indices and i >= 0 and i < len(toc_with_page_number):
                physical_index = toc_with_page_number[i].get('physical_index')
                if physical_index is not None:
                    prev_correct = physical_index
                    break
        # If no previous correct item found, use start_index
        if prev_correct is None:
            prev_correct = start_index - 1
        
        # Find the next correct item
        next_correct = None
        for i in range(list_index+1, len(toc_with_page_number)):
            if i not in incorrect_indices and i >= 0 and i < len(toc_with_page_number):
                physical_index = toc_with_page_number[i].get('physical_index')
                if physical_index is not None:
                    next_correct = physical_index
                    break
        # If no next correct item found, use end_index
        if next_correct is None:
            next_correct = end_index
        
        incorrect_results_and_range_logs.append({
            'list_index': list_index,
            'title': incorrect_item['title'],
            'prev_correct': prev_correct,
            'next_correct': next_correct
        })

        page_contents=[]
        for page_index in range(prev_correct, next_correct+1):
            # Add bounds checking to prevent IndexError
            page_list_idx = page_index - start_index
            if page_list_idx >= 0 and page_list_idx < len(page_list):
                page_text = f"<physical_index_{page_index}>\n{page_list[page_list_idx][0]}\n<physical_index_{page_index}>\n\n"
                page_contents.append(page_text)
            else:
                continue
        content_range = ''.join(page_contents)
        
        physical_index_int = await single_toc_item_index_fixer(incorrect_item['title'], content_range, model)
        
        # Check if the result is correct
        check_item = incorrect_item.copy()
        check_item['physical_index'] = physical_index_int
        check_result = await check_title_appearance(check_item, page_list, start_index, model)

        return {
            'list_index': list_index,
            'title': incorrect_item['title'],
            'physical_index': physical_index_int,
            'is_valid': check_result['answer'] == 'yes'
        }

    # Process incorrect items concurrently
    tasks = [
        process_and_check_item(item)
        for item in incorrect_results
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for item, result in zip(incorrect_results, results):
        if isinstance(result, Exception):
            print(f"Processing item {item} generated an exception: {result}")
            continue
    results = [result for result in results if not isinstance(result, Exception)]

    # Update the toc_with_page_number with the fixed indices and check for any invalid results
    invalid_results = []
    for result in results:
        if result['is_valid']:
            # Add bounds checking to prevent IndexError
            list_idx = result['list_index']
            if 0 <= list_idx < len(toc_with_page_number):
                toc_with_page_number[list_idx]['physical_index'] = result['physical_index']
            else:
                # Index is out of bounds, treat as invalid
                invalid_results.append({
                    'list_index': result['list_index'],
                    'title': result['title'],
                    'physical_index': result['physical_index'],
                })
        else:
            invalid_results.append({
                'list_index': result['list_index'],
                'title': result['title'],
                'physical_index': result['physical_index'],
            })

    logger.info(f'incorrect_results_and_range_logs: {incorrect_results_and_range_logs}')
    logger.info(f'invalid_results: {invalid_results}')

    return toc_with_page_number, invalid_results



async def fix_incorrect_toc_with_retries(toc_with_page_number, page_list, incorrect_results, start_index=1, max_attempts=3, model=None, logger=None):
    print('start fix_incorrect_toc')
    fix_attempt = 0
    current_toc = toc_with_page_number
    current_incorrect = incorrect_results

    while current_incorrect:
        print(f"Fixing {len(current_incorrect)} incorrect results")
        
        current_toc, current_incorrect = await fix_incorrect_toc(current_toc, page_list, current_incorrect, start_index, model, logger)
                
        fix_attempt += 1
        if fix_attempt >= max_attempts:
            logger.info("Maximum fix attempts reached")
            break
    
    return current_toc, current_incorrect




################### verify toc #########################################################
async def verify_toc(page_list, list_result, start_index=1, N=None, model=None):
    print('start verify_toc')
    # Find the last non-None physical_index
    last_physical_index = None
    for item in reversed(list_result):
        if item.get('physical_index') is not None:
            last_physical_index = item['physical_index']
            break
    
    # Early return if we don't have valid physical indices
    if last_physical_index is None or last_physical_index < len(page_list)/2:
        return 0, []
    
    # Determine which items to check
    if N is None:
        print('check all items')
        sample_indices = range(0, len(list_result))
    else:
        N = min(N, len(list_result))
        print(f'check {N} items')
        sample_indices = random.sample(range(0, len(list_result)), N)

    # Prepare items with their list indices
    indexed_sample_list = []
    for idx in sample_indices:
        item = list_result[idx]
        # Skip items with None physical_index (these were invalidated by validate_and_truncate_physical_indices)
        if item.get('physical_index') is not None:
            item_with_index = item.copy()
            item_with_index['list_index'] = idx  # Add the original index in list_result
            indexed_sample_list.append(item_with_index)

    # Run checks concurrently. return_exceptions=True: a transient LLM failure
    # on one sampled item must degrade that item to 'no' (same as an
    # unavailable physical_index above), not abort verification for the
    # whole document.
    tasks = [
        check_title_appearance(item, page_list, start_index, model)
        for item in indexed_sample_list
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    results = []
    for item, result in zip(indexed_sample_list, raw_results):
        if isinstance(result, Exception):
            results.append({'list_index': item.get('list_index'), 'answer': 'no',
                            'title': item.get('title'), 'page_number': item.get('physical_index')})
        else:
            results.append(result)
    
    # Process results
    correct_count = 0
    incorrect_results = []
    for result in results:
        if result['answer'] == 'yes':
            correct_count += 1
        else:
            incorrect_results.append(result)
    
    # Calculate accuracy
    checked_count = len(results)
    accuracy = correct_count / checked_count if checked_count > 0 else 0
    print(f"accuracy: {accuracy*100:.2f}%")
    return accuracy, incorrect_results





################### main process #########################################################
async def meta_processor(page_list, mode=None, toc_content=None, toc_page_list=None, start_index=1, opt=None, logger=None, doc=None):
    print(mode)
    print(f'start_index: {start_index}')

    if mode == 'process_toc_with_page_numbers':
        toc_with_page_number = process_toc_with_page_numbers(toc_content, toc_page_list, page_list, toc_check_page_num=opt.toc_check_page_num, model=opt.model, logger=logger, doc=doc)
    elif mode == 'process_toc_no_page_numbers':
        toc_with_page_number = process_toc_no_page_numbers(toc_content, toc_page_list, page_list, model=opt.model, logger=logger, doc=doc)
    else:
        toc_with_page_number = process_no_toc(page_list, start_index=start_index, model=opt.model, logger=logger)
            
    toc_with_page_number = [item for item in toc_with_page_number if item.get('physical_index') is not None]

    toc_with_page_number = validate_and_truncate_physical_indices(
        toc_with_page_number,
        len(page_list),
        start_index=start_index,
        logger=logger
    )

    # Short-circuit: if the TOC came from an authoritative source (PyMuPDF bookmarks),
    # skip verify_toc. The LLM cannot reliably re-check 1000+ page numbers against page
    # text (we measured ~21% accuracy on USB4 with deepseek-v4-flash), and verify_toc
    # dominates wall-clock time on token-plan gateways even with concurrency throttling.
    if toc_with_page_number and all(item.get('from_pdf_toc') for item in toc_with_page_number):
        logger.info({'skipped_verify_toc': True, 'reason': 'TOC from PyMuPDF bookmarks', 'count': len(toc_with_page_number)})
        progress_log(f'meta_processor: skipping verify_toc ({len(toc_with_page_number)} entries, from_pdf_toc)')
        print(f'skip verify_toc: TOC from PyMuPDF bookmarks ({len(toc_with_page_number)} entries)')
        return toc_with_page_number

    accuracy, incorrect_results = await verify_toc(page_list, toc_with_page_number, start_index=start_index, model=opt.model)
        
    logger.info({
        'mode': 'process_toc_with_page_numbers',
        'accuracy': accuracy,
        'incorrect_results': incorrect_results
    })
    if accuracy == 1.0 and len(incorrect_results) == 0:
        return toc_with_page_number
    if accuracy > 0.6 and len(incorrect_results) > 0:
        toc_with_page_number, incorrect_results = await fix_incorrect_toc_with_retries(toc_with_page_number, page_list, incorrect_results,start_index=start_index, max_attempts=3, model=opt.model, logger=logger)
        return toc_with_page_number
    else:
        if mode == 'process_toc_with_page_numbers':
            return await meta_processor(page_list, mode='process_toc_no_page_numbers', toc_content=toc_content, toc_page_list=toc_page_list, start_index=start_index, opt=opt, logger=logger)
        elif mode == 'process_toc_no_page_numbers':
            return await meta_processor(page_list, mode='process_no_toc', start_index=start_index, opt=opt, logger=logger)
        else:
            raise Exception('Processing failed')
        
 
async def process_large_node_recursively(node, page_list, opt=None, logger=None, skip_llm_reprocessing=False):
    node_page_list = page_list[node['start_index']-1:node['end_index']]
    token_num = sum([page[1] for page in node_page_list])
    
    # Only re-derive sub-structure via process_no_toc when the node is large AND
    # has no existing children AND LLM reprocessing is not explicitly disabled
    # (e.g. when the TOC came from an authoritative source like PyMuPDF bookmarks).
    if (node['end_index'] - node['start_index'] > opt.max_page_num_each_node
            and token_num >= opt.max_token_num_each_node
            and not node.get('nodes')
            and not skip_llm_reprocessing):
        print('large node:', node['title'], 'start_index:', node['start_index'], 'end_index:', node['end_index'], 'token_num:', token_num)

        node_toc_tree = await meta_processor(node_page_list, mode='process_no_toc', start_index=node['start_index'], opt=opt, logger=logger)
        node_toc_tree = await check_title_appearance_in_start_concurrent(node_toc_tree, page_list, model=opt.model, logger=logger)
        
        # Filter out items with None physical_index before post_processing
        valid_node_toc_items = [item for item in node_toc_tree if item.get('physical_index') is not None]
        
        if valid_node_toc_items and node['title'].strip() == valid_node_toc_items[0]['title'].strip():
            node['nodes'] = post_processing(valid_node_toc_items[1:], node['end_index'])
            node['end_index'] = valid_node_toc_items[1]['start_index'] if len(valid_node_toc_items) > 1 else node['end_index']
        else:
            node['nodes'] = post_processing(valid_node_toc_items, node['end_index'])
            node['end_index'] = valid_node_toc_items[0]['start_index'] if valid_node_toc_items else node['end_index']
    elif (node['end_index'] - node['start_index'] > opt.max_page_num_each_node
            and token_num >= opt.max_token_num_each_node
            and node.get('nodes')):
        if logger:
            logger.info({'skipped_process_no_toc': True, 'reason': 'node already has children', 'title': node.get('title'), 'children': len(node['nodes'])})
        progress_log(f"process_large_node_recursively: skipping process_no_toc — '{node.get('title', '?')}' already has {len(node['nodes'])} children")
        print(f"skip process_no_toc for {node.get('title', '?')}: already has {len(node['nodes'])} children")
        
    if 'nodes' in node and node['nodes']:
        tasks = [
            process_large_node_recursively(child_node, page_list, opt, logger=logger, skip_llm_reprocessing=skip_llm_reprocessing)
            for child_node in node['nodes']
        ]
        # return_exceptions=True: one child subtree failing to expand further
        # must not abort the whole document — it's left as a leaf at its
        # current boundaries instead.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for child_node, result in zip(node['nodes'], results):
            if isinstance(result, Exception) and logger:
                logger.error(f"Failed to expand node '{child_node.get('title')}': {result}")
    
    return node

async def tree_parser(page_list, opt, doc=None, logger=None):
    # If the PDF has embedded bookmarks (PyMuPDF TOC), skip the LLM-based
    # TOC detection entirely. PyMuPDF bookmarks are authoritative — they carry
    # the exact TOC structure with page numbers — so there is no need for the
    # fallible LLM pipeline (toc_detector_single_page + detect_page_index).
    pdf_has_bookmarks = False
    if doc and isinstance(doc, str) and doc.lower().endswith('.pdf'):
        set_doc_name(os.path.splitext(os.path.basename(doc))[0])
        try:
            import fitz
            _bm_doc = fitz.open(doc)
            _raw = _bm_doc.get_toc(simple=False)
            _bm_doc.close()
            if _raw:
                pdf_has_bookmarks = True
                progress_log(f'tree_parser: {len(_raw)} bookmarks detected, using PyMuPDF TOC (skipping LLM check_toc)')
                logger.info({'pdf_bookmarks_detected': len(_raw), 'skipping_llm_toc_detection': True})
        except Exception:
            pass
    if not pdf_has_bookmarks:
        progress_log('tree_parser: no bookmarks, falling back to LLM check_toc')

    if pdf_has_bookmarks:
        # Bypass check_toc(); go straight to process_toc_with_page_numbers.
        # process_toc_with_page_numbers already short-circuits to
        # toc_transformer_from_pdf_toc() when doc is provided.
        toc_with_page_number = await meta_processor(
            page_list,
            mode='process_toc_with_page_numbers',
            start_index=1,
            toc_content='',        # unused when PyMuPDF bookmarks are available
            toc_page_list=[],      # unused when PyMuPDF bookmarks are available
            opt=opt,
            logger=logger,
            doc=doc)
    else:
        check_toc_result = check_toc(page_list, opt)
        logger.info(check_toc_result)

        if check_toc_result.get("toc_content") and check_toc_result["toc_content"].strip() and check_toc_result["page_index_given_in_toc"] == "yes":
            toc_with_page_number = await meta_processor(
                page_list,
                mode='process_toc_with_page_numbers',
                start_index=1,
                toc_content=check_toc_result['toc_content'],
                toc_page_list=check_toc_result['toc_page_list'],
                opt=opt,
                logger=logger,
                doc=doc)
        else:
            toc_with_page_number = await meta_processor(
                page_list,
                mode='process_no_toc',
                start_index=1,
                opt=opt,
                logger=logger,
                doc=doc)

    toc_with_page_number = add_preface_if_needed(toc_with_page_number)
    
    # Run LLM-based title appearance check for accurate end_index boundaries.
    # Even for PyMuPDF bookmark TOCs, this correctly determines whether a
    # section title appears at the start of its page — which affects how
    # end_index is computed in post_processing().
    toc_with_page_number = await check_title_appearance_in_start_concurrent(toc_with_page_number, page_list, model=opt.model, logger=logger)
    
    # Filter out items with None physical_index before post_processings
    valid_toc_items = [item for item in toc_with_page_number if item.get('physical_index') is not None]
    
    reversed_count = sum(1 for item in toc_with_page_number if item.get('end_index', 0) < item.get('start_index', 0))
    if reversed_count:
        progress_log(f'tree_parser: WARNING — {reversed_count} nodes with reversed page ranges (end < start)')
    toc_tree = post_processing(valid_toc_items, len(page_list))
    tasks = [
        process_large_node_recursively(node, page_list, opt, logger=logger, skip_llm_reprocessing=pdf_has_bookmarks)
        for node in toc_tree
    ]
    # return_exceptions=True: one top-level node failing to expand further
    # must not abort indexing the whole document.
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for node, result in zip(toc_tree, results):
        if isinstance(result, Exception) and logger:
            logger.error(f"Failed to expand node '{node.get('title')}': {result}")
    
    return toc_tree


def page_index_main(doc, opt=None):
    logger = JsonLogger(doc)
    
    is_valid_pdf = (
        (isinstance(doc, str) and os.path.isfile(doc) and doc.lower().endswith(".pdf")) or 
        isinstance(doc, BytesIO)
    )
    if not is_valid_pdf:
        raise ValueError("Unsupported input type. Expected a PDF file path or BytesIO object.")

    print('Parsing PDF...')
    page_list = get_page_tokens(doc, model=opt.model)

    logger.info({'total_page_number': len(page_list)})
    logger.info({'total_token': sum([page[1] for page in page_list])})

    async def page_index_builder():
        structure = await tree_parser(page_list, opt, doc=doc, logger=logger)
        if opt.if_add_node_id:
            write_node_id(structure)
        if opt.if_add_node_text:
            add_node_text(structure, page_list)
        if opt.if_add_node_summary:
            if not opt.if_add_node_text:
                add_node_text(structure, page_list)
            await generate_summaries_for_structure(structure, model=opt.model)
            if not opt.if_add_node_text:
                remove_structure_text(structure)
            if opt.if_add_doc_description:
                # Create a clean structure without unnecessary fields for description generation
                clean_structure = create_clean_structure_for_description(structure)
                doc_description = generate_doc_description(clean_structure, model=opt.model)
                structure = format_structure(structure, order=['title', 'node_id', 'start_index', 'end_index', 'summary', 'text', 'nodes'])
                return {
                    'doc_name': get_pdf_name(doc),
                    'doc_description': doc_description,
                    'structure': structure,
                }
        structure = format_structure(structure, order=['title', 'node_id', 'start_index', 'end_index', 'summary', 'text', 'nodes'])
        return {
            'doc_name': get_pdf_name(doc),
            'structure': structure,
        }

    return asyncio.run(page_index_builder())


def page_index(doc, model=None, toc_check_page_num=None, max_page_num_each_node=None, max_token_num_each_node=None,
               if_add_node_id=None, if_add_node_summary=None, if_add_doc_description=None, if_add_node_text=None):
    from ..config import IndexConfig

    # Explicit dict of the named kwargs — NOT locals(), which would also
    # capture any local variable defined above this line (e.g. the IndexConfig
    # import itself) and get rejected by IndexConfig(extra="forbid").
    user_opt = {
        "model": model,
        "toc_check_page_num": toc_check_page_num,
        "max_page_num_each_node": max_page_num_each_node,
        "max_token_num_each_node": max_token_num_each_node,
        "if_add_node_id": if_add_node_id,
        "if_add_node_summary": if_add_node_summary,
        "if_add_doc_description": if_add_doc_description,
        "if_add_node_text": if_add_node_text,
    }
    user_opt = {k: v for k, v in user_opt.items() if v is not None}
    opt = IndexConfig(**user_opt)
    return page_index_main(doc, opt)


def validate_and_truncate_physical_indices(toc_with_page_number, page_list_length, start_index=1, logger=None):
    """
    Validates and truncates physical indices that exceed the actual document length.
    This prevents errors when TOC references pages that don't exist in the document (e.g. the file is broken or incomplete).
    """
    if not toc_with_page_number:
        return toc_with_page_number
    
    max_allowed_page = page_list_length + start_index - 1
    truncated_items = []
    
    for i, item in enumerate(toc_with_page_number):
        if item.get('physical_index') is not None:
            original_index = item['physical_index']
            if original_index > max_allowed_page:
                item['physical_index'] = None
                truncated_items.append({
                    'title': item.get('title', 'Unknown'),
                    'original_index': original_index
                })
                if logger:
                    logger.info(f"Removed physical_index for '{item.get('title', 'Unknown')}' (was {original_index}, too far beyond document)")
    
    if truncated_items and logger:
        logger.info(f"Total removed items: {len(truncated_items)}")
        
    print(f"Document validation: {page_list_length} pages, max allowed index: {max_allowed_page}")
    if truncated_items:
        print(f"Truncated {len(truncated_items)} TOC items that exceeded document length")
     
    return toc_with_page_number