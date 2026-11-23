import pathlib
p = pathlib.Path('F:/Codex Xiangm/amd-notebook/breezeblue-manager/app/main.py')
with open('F:/Codex Xiangm/amd-notebook/breezeblue-manager/gen_main_content.py', 'r', encoding='utf-8') as f:
    content = f.read()
p.write_text(content, encoding='utf-8')
print('main.py written')
