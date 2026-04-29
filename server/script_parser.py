import re
from pathlib import Path
from typing import Dict, Optional

class ScriptParser:
    """解析演讲稿md文件，按'第X页：'格式分割内容"""
    
    # 支持阿拉伯数字和中文数字
    PAGE_PATTERN = re.compile(r'^第\s*(\d+|[一二三四五六七八九十百零]+)\s*页\s*[:：]\s*$', re.MULTILINE)
    
    # 中文数字映射
    CN_NUMBERS = {
        '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
        '零': 0, '百': 100
    }
    
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.pages: Dict[int, str] = {}
        self._parse()
    
    @classmethod
    def _cn_to_int(cls, cn: str) -> int:
        """中文数字转整数"""
        if cn.isdigit():
            return int(cn)
        
        total = 0
        temp = 0
        for char in cn:
            num = cls.CN_NUMBERS.get(char, 0)
            if num == 100:
                if temp == 0:
                    temp = 1
                total += temp * 100
                temp = 0
            elif num == 10:
                if temp == 0:
                    temp = 1
                total += temp * 10
                temp = 0
            else:
                temp += num
        return total + temp
    
    def _parse(self) -> None:
        if not self.file_path.exists():
            raise FileNotFoundError(f"演讲稿文件不存在: {self.file_path}")
        
        content = self.file_path.read_text(encoding='utf-8')
        
        # 找到所有页码标记的位置
        matches = list(self.PAGE_PATTERN.finditer(content))
        
        if not matches:
            raise ValueError(f"文件格式错误，未找到'第X页：'标记: {self.file_path}")
        
        for i, match in enumerate(matches):
            page_num = self._cn_to_int(match.group(1))
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            page_content = content[start:end].strip()
            self.pages[page_num] = page_content
    
    def get_page(self, page_num: int) -> Optional[str]:
        return self.pages.get(page_num)
    
    def get_total_pages(self) -> int:
        return max(self.pages.keys()) if self.pages else 0
    
    def has_page(self, page_num: int) -> bool:
        return page_num in self.pages

    @classmethod
    def from_file(cls, file_path: Path) -> 'ScriptParser':
        return cls(file_path)
