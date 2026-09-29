# tools/ast_parser.py
import os
import re
from tree_sitter import Language, Parser, Query, QueryCursor
import tree_sitter_python
import tree_sitter_javascript
import tree_sitter_java

# 1. Initialize the pre-compiled language grammars
LANGUAGES = {
    "python": Language(tree_sitter_python.language()),
    "javascript": Language(tree_sitter_javascript.language()),
    "java": Language(tree_sitter_java.language()),
}

# 2. Define S-expression queries to find Classes and Functions
QUERIES = {
    "python": """
        (function_definition name: (identifier) @name) @function
        (class_definition name: (identifier) @name) @class
    """,
    "javascript": """
        (function_declaration name: (identifier) @name) @function
        (method_definition name: (property_identifier) @name) @function
        (class_declaration name: (identifier) @name) @class
        (lexical_declaration (variable_declarator name: (identifier) @name value: (arrow_function))) @function
        (variable_declaration (variable_declarator name: (identifier) @name value: (arrow_function))) @function
    """,
    "java": """
        (method_declaration name: (identifier) @name) @function
        (class_declaration name: (identifier) @name) @class
    """
}

# 3. Map file extensions to language profiles and comment specs
EXTENSION_MAP = {
    ".py": {"lang": "python", "style": "Python triple-quote docstring structure (組み立て: \"\"\" comment \"\"\")"},
    ".js": {"lang": "javascript", "style": "Standard JSDoc multiline block format (組み立て: /** comment */)"},
    ".jsx": {"lang": "javascript", "style": "Standard JSDoc multiline block format (組み立て: /** comment */)"},
    ".ts": {"lang": "typescript", "style": "Standard TSDoc multiline block format (/** comment */)"},
    ".tsx": {"lang": "typescript", "style": "Standard TSDoc multiline block format (/** comment */)"},
    ".java": {"lang": "java", "style": "Standard Javadoc multiline block format (組み立て: /** comment */)"},
    ".go": {"lang": "go", "style": "Standard Go documentation comment"},
    ".rb": {"lang": "ruby", "style": "Ruby hash comment"},
    ".rs": {"lang": "rust", "style": "Rust documentation comment"},
    ".c": {"lang": "c", "style": "C documentation block comment"},
    ".h": {"lang": "c", "style": "C documentation block comment"},
    ".cpp": {"lang": "cpp", "style": "C++ documentation block comment"},
    ".hpp": {"lang": "cpp", "style": "C++ documentation block comment"},
    ".cs": {"lang": "csharp", "style": "C# documentation block comment"},
    ".php": {"lang": "php", "style": "PHPDoc block comment"},
    ".swift": {"lang": "swift", "style": "Swift documentation comment"},
    ".kt": {"lang": "kotlin", "style": "KDoc block comment"},
    ".kts": {"lang": "kotlin", "style": "KDoc block comment"},
    ".scala": {"lang": "scala", "style": "Scaladoc block comment"},
    ".sh": {"lang": "shell", "style": "Shell hash comment"},
    ".bash": {"lang": "shell", "style": "Shell hash comment"},
    ".sql": {"lang": "sql", "style": "SQL line comment"},
}


# Conservative declaration patterns cover supported logic languages that do not yet
# have a bundled Tree-sitter grammar. They deliberately avoid matching ordinary
# statements: a missed symbol is safer than inserting a comment inside an expression.
DECLARATION_PATTERNS = {
    "typescript": [
        ("class", r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:class|interface|enum|namespace)\s+([A-Za-z_$][\w$]*)"),
        ("function", r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"),
        ("function", r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^\n]*\)|[A-Za-z_$][\w$]*)\s*=>"),
    ],
    "go": [
        ("class", r"^\s*type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b"),
        ("function", r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("),
    ],
    "ruby": [
        ("class", r"^\s*(?:class|module)\s+([A-Za-z_]\w*(?:::\w+)*)"),
        ("function", r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[!?=]?)"),
    ],
    "rust": [
        ("class", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type)\s+([A-Za-z_]\w*)"),
        ("function", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)"),
    ],
    "c": [
        ("class", r"^\s*(?:typedef\s+)?(?:struct|enum|union)\s+([A-Za-z_]\w*)"),
        ("function", r"^\s*(?!if\b|for\b|while\b|switch\b)(?:[A-Za-z_]\w*[\s*]+)+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{?\s*$"),
    ],
    "cpp": [
        ("class", r"^\s*(?:template\s*<[^>]+>\s*)?(?:class|struct|enum)\s+([A-Za-z_]\w*)"),
        ("function", r"^\s*(?!if\b|for\b|while\b|switch\b)(?:template\s*<[^>]+>\s*)?(?:[\w:<>,~&*]+\s+)+([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:const\s*)?\{?\s*$"),
    ],
    "csharp": [
        ("class", r"^\s*(?:(?:public|private|protected|internal|static|abstract|sealed|partial)\s+)*(?:class|interface|record|struct|enum)\s+([A-Za-z_]\w*)"),
        ("function", r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|abstract|async|sealed|new)\s+)+[\w<>,?\[\].]+\s+([A-Za-z_]\w*)\s*\("),
    ],
    "php": [
        ("class", r"^\s*(?:(?:abstract|final|readonly)\s+)*(?:class|interface|trait|enum)\s+([A-Za-z_]\w*)"),
        ("function", r"^\s*(?:(?:public|private|protected|static|final|abstract)\s+)*function\s+&?\s*([A-Za-z_]\w*)"),
    ],
    "swift": [
        ("class", r"^\s*(?:(?:public|private|internal|open|final)\s+)*(?:class|struct|protocol|enum|actor)\s+([A-Za-z_]\w*)"),
        ("function", r"^\s*(?:(?:public|private|internal|open|static|class|mutating|async)\s+)*func\s+([A-Za-z_]\w*)"),
    ],
    "kotlin": [
        ("class", r"^\s*(?:(?:public|private|protected|internal|open|abstract|sealed|data|value)\s+)*(?:class|interface|object|enum\s+class)\s+([A-Za-z_]\w*)"),
        ("function", r"^\s*(?:(?:public|private|protected|internal|open|override|suspend|inline|operator)\s+)*fun\s+(?:<[^>]+>\s*)?([A-Za-z_]\w*)"),
    ],
    "scala": [
        ("class", r"^\s*(?:(?:sealed|abstract|final|case)\s+)*(?:class|trait|object|enum)\s+([A-Za-z_]\w*)"),
        ("function", r"^\s*(?:(?:private|protected|override|final|implicit|inline)\s+)*def\s+([A-Za-z_]\w*)"),
    ],
    "shell": [
        ("function", r"^\s*(?:function\s+)?([A-Za-z_]\w*)\s*(?:\(\s*\))?\s*\{"),
    ],
    "sql": [
        ("class", r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|TYPE)\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?([A-Za-z_]\w*)"),
        ("function", r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE|TRIGGER)\s+[\"`\[]?([A-Za-z_]\w*)"),
    ],
}


def _extract_pattern_chunks(source_code: str, language_key: str, comment_style: str) -> list[dict]:
    lines = source_code.splitlines()
    chunks: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for tag, pattern in DECLARATION_PATTERNS.get(language_key, []):
        for match in re.finditer(pattern, source_code, flags=re.MULTILINE | (re.IGNORECASE if language_key == "sql" else 0)):
            start_line = source_code.count("\n", 0, match.start()) + 1
            key = (tag, start_line)
            if key in seen:
                continue
            seen.add(key)
            end_line = min(len(lines), start_line + 12)
            chunks.append({
                "type": tag,
                "name": match.group(1),
                "code": "\n".join(lines[start_line - 1:end_line]),
                "start_line": start_line,
                "end_line": end_line,
                "language": language_key,
                "comment_style": comment_style,
            })
    return sorted(chunks, key=lambda chunk: chunk["start_line"])


class UniversalExtractor:
    def __init__(self, source_code: str, extension: str):
        self.source_code = source_code
        self.source_lines = source_code.splitlines()
        self.ext_profile = EXTENSION_MAP[extension]
        self.language_key = self.ext_profile["lang"]
        self.comment_style = self.ext_profile["style"]
        self.language = LANGUAGES[self.language_key]
        self.parser = Parser(self.language)

    def extract_chunks(self) -> list[dict]:
        """Parses the code using Tree-sitter's new v0.22+ API and appends language targeting info."""
        tree = self.parser.parse(bytes(self.source_code, "utf8"))
        
        query_string = QUERIES[self.language_key]
        query = Query(self.language, query_string)
        
        cursor = QueryCursor(query)
        matches = cursor.matches(tree.root_node)
        
        chunks = []
        
        for pattern_index, match_dict in matches:
            tag = None
            main_node = None
            
            if "function" in match_dict:
                tag = "function"
                main_node = match_dict["function"][0]
            elif "class" in match_dict:
                tag = "class"
                main_node = match_dict["class"][0]
                
            if tag and main_node:
                name = "Unknown"
                if "name" in match_dict and len(match_dict["name"]) > 0:
                    name_node = match_dict["name"][0]
                    if hasattr(name_node, 'text') and name_node.text:
                        name = name_node.text.decode('utf-8')
                    else:
                        name = self.source_code[name_node.start_byte:name_node.end_byte]
                        
                start_line = main_node.start_point[0] + 1
                end_line = main_node.end_point[0] + 1
                code_segment = "\n".join(self.source_lines[start_line - 1 : end_line])
                
                # We inject language parameters directly into the chunk context block
                chunks.append({
                    "type": tag,
                    "name": name,
                    "code": code_segment,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": self.language_key,
                    "comment_style": self.comment_style
                })
                
        return chunks


def parse_code_file(file_path: str) -> list[dict]:
    """
    Main entry point for parsing any supported code file.
    Automatically detects the language, parses it, and returns the structural chunks with styling meta.
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext not in EXTENSION_MAP:
        return []
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source_code = f.read()
            
        profile = EXTENSION_MAP[ext]
        if profile["lang"] in LANGUAGES:
            extractor = UniversalExtractor(source_code, ext)
            return extractor.extract_chunks()
        return _extract_pattern_chunks(source_code, profile["lang"], profile["style"])
        
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return []
