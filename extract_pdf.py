# -*- coding: utf-8 -*-
import pdfplumber
import sys

pdf_path = r'D:\Desktop\usenixsecurity23-chiesa.pdf'
output_txt = r'D:\Desktop\usenixsecurity23-chiesa.txt'
output_md = r'D:\Desktop\usenixsecurity23-chiesa.md'

try:
    print(f'正在打开: {pdf_path}')
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        print(f'总页数: {total}')
        
        all_text = []
        for i, page in enumerate(pdf.pages):
            sys.stdout.write(f'\r处理第 {i+1}/{total} 页...')
            sys.stdout.flush()
            text = page.extract_text()
            if text:
                all_text.append(f'\n--- Page {i+1} ---\n{text}')
        
        print('\n正在保存文件...')
        
        # 保存 txt
        with open(output_txt, 'w', encoding='utf-8') as f:
            f.write(''.join(all_text))
        print(f'已保存: {output_txt}')
        
        # 保存 md
        md_content = '# Usenix Security 23 - Chiesa\n\n' + ''.join(all_text)
        with open(output_md, 'w', encoding='utf-8') as f:
            f.write(md_content)
        print(f'已保存: {output_md}')
        
        print('\n完成!')

except Exception as e:
    print(f'\n错误: {e}')
    import traceback
    traceback.print_exc()
