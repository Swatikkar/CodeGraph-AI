# tools/ast_parser.py
import os
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
    ".java": {"lang": "java", "style": "Standard Javadoc multiline block format (組み立て: /** comment */)"}
}


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
            
        extractor = UniversalExtractor(source_code, ext)
        return extractor.extract_chunks()
        
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return []