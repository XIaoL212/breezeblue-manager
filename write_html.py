import pathlib
html = '<!DOCTYPE html>'
p = pathlib.Path('F:/Codex Xiangm/amd-notebook/breezeblue-manager/app/static/index.html')
p.write_text(html, encoding='utf-8')
print('ok')
