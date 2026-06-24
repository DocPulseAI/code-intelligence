import re

class SchemaDetector:
    """
    Detects database schema changes in code.
    US-14: Schema Changes

    Supports:
    - Java JPA (@Entity annotations)
    - SQL DDL statements (CREATE/ALTER/DROP TABLE)
    - Mongoose schemas (mongoose.Schema, mongoose.model)
    - Django models (models.Model)
    """

    # Java JPA
    JAVA_ENTITY = re.compile(r'@Entity', re.MULTILINE)

    # SQL DDL
    SQL_DDL = re.compile(r"\b(CREATE|ALTER|DROP)\s+TABLE\b", re.IGNORECASE)
    SQL_CREATE_TABLE = re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)
    SQL_ALTER_TABLE = re.compile(r"\bALTER\s+TABLE\s+(?:ONLY\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)
    SQL_DROP_TABLE = re.compile(r"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)

    # SQL Foreign Keys
    # ALTER TABLE foo ADD CONSTRAINT bar FOREIGN KEY (x) REFERENCES target_table(id)
    PG_ALTER_ADD_FKEY = re.compile(r"\bALTER\s+TABLE\s+(?:ONLY\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?.*?FOREIGN\s+KEY\s*\([^)]+\)\s*REFERENCES\s+(?:[A-Za-z_][A-Za-z0-9_]*\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE | re.DOTALL)
    # CREATE TABLE foo ( ... FOREIGN KEY (x) REFERENCES target_table(id) ... )
    PG_CREATE_TABLE_BODY_FKEY = re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s*\((.*?)\)", re.IGNORECASE | re.DOTALL)
    PG_INLINE_FKEY = re.compile(r"REFERENCES\s+(?:[A-Za-z_][A-Za-z0-9_]*\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)

    # PostgreSQL-specific DDL
    PG_CREATE_TYPE = re.compile(r"\bCREATE\s+TYPE\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s+AS\s+ENUM\b", re.IGNORECASE)
    PG_ALTER_TYPE = re.compile(r"\bALTER\s+TYPE\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s+ADD\s+VALUE\b", re.IGNORECASE)
    PG_CREATE_INDEX = re.compile(r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s+ON\s+(?:[A-Za-z_][A-Za-z0-9_]*\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)
    PG_DROP_INDEX = re.compile(r"\bDROP\s+INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+EXISTS\s+)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)
    PG_ADD_CONSTRAINT = re.compile(r"\bADD\s+CONSTRAINT\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)
    PG_DROP_CONSTRAINT = re.compile(r"\bDROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)
    PG_ADD_COLUMN = re.compile(r"\bADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)
    PG_DROP_COLUMN = re.compile(r"\bDROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)
    PG_ALTER_COLUMN_TYPE = re.compile(r"\bALTER\s+COLUMN\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s+TYPE\b", re.IGNORECASE)

    # Mongoose (JavaScript/TypeScript)
    MONGOOSE_SCHEMA = re.compile(r'new\s+(?:mongoose\.)?Schema\s*\(', re.MULTILINE)
    MONGOOSE_MODEL = re.compile(r'mongoose\.model\s*\(\s*[\'"](\w+)[\'"]', re.MULTILINE)

    # Django ORM (Python)
    DJANGO_MODEL = re.compile(r'class\s+\w+\s*\(\s*(?:models\.)?Model\s*\)', re.MULTILINE)

    @staticmethod
    def analyze(file_path, content):
        """
        Analyze file content for schema-related patterns.

        Args:
            file_path: Path to the file
            content: File content string

        Returns:
            List of schema tags (e.g., ['JPA_ENTITY'], ['MONGOOSE_SCHEMA'])
        """
        tags = []
        ext = file_path.lower().split('.')[-1] if '.' in file_path else ''

        # Java JPA Entity
        if ext == 'java' and SchemaDetector.JAVA_ENTITY.search(content):
            tags.append("JPA_ENTITY")

        # SQL DDL
        elif ext == "sql":
            sql_tags = set()
            has_generic_ddl = bool(SchemaDetector.SQL_DDL.search(content))
            if has_generic_ddl:
                sql_tags.add("SQL_SCHEMA_CHANGE")

            # Generic table-level tags.
            for match in SchemaDetector.SQL_CREATE_TABLE.finditer(content):
                sql_tags.add("SQL_TABLE_CHANGE")
                sql_tags.add(f"SQL_CREATE_TABLE:{match.group(1)}")
            for match in SchemaDetector.SQL_ALTER_TABLE.finditer(content):
                sql_tags.add("SQL_TABLE_CHANGE")
                sql_tags.add(f"SQL_ALTER_TABLE:{match.group(1)}")
            for match in SchemaDetector.SQL_DROP_TABLE.finditer(content):
                sql_tags.add("SQL_TABLE_CHANGE")
                sql_tags.add(f"SQL_DROP_TABLE:{match.group(1)}")

            # SQL Foreign Key mappings
            for match in SchemaDetector.PG_ALTER_ADD_FKEY.finditer(content):
                src_table = match.group(1)
                target_table = match.group(2)
                sql_tags.add(f"SQL_FOREIGN_KEY:{src_table}:{target_table}")
                
            for match in SchemaDetector.PG_CREATE_TABLE_BODY_FKEY.finditer(content):
                src_table = match.group(1)
                body = match.group(2)
                for fkey_match in SchemaDetector.PG_INLINE_FKEY.finditer(body):
                    target_table = fkey_match.group(1)
                    sql_tags.add(f"SQL_FOREIGN_KEY:{src_table}:{target_table}")

            # PostgreSQL-specific tags.
            pg_hit = False
            for match in SchemaDetector.PG_CREATE_TYPE.finditer(content):
                pg_hit = True
                sql_tags.add("POSTGRES_SCHEMA_CHANGE")
                sql_tags.add("POSTGRES_ENUM_CHANGE")
                sql_tags.add(f"POSTGRES_CREATE_TYPE:{match.group(1)}")
            for match in SchemaDetector.PG_ALTER_TYPE.finditer(content):
                pg_hit = True
                sql_tags.add("POSTGRES_SCHEMA_CHANGE")
                sql_tags.add("POSTGRES_ENUM_CHANGE")
                sql_tags.add(f"POSTGRES_ALTER_TYPE:{match.group(1)}")
            for match in SchemaDetector.PG_CREATE_INDEX.finditer(content):
                pg_hit = True
                sql_tags.add("POSTGRES_SCHEMA_CHANGE")
                sql_tags.add("POSTGRES_INDEX_CHANGE")
                sql_tags.add(f"POSTGRES_CREATE_INDEX:{match.group(1)}")
                sql_tags.add(f"POSTGRES_INDEX_TABLE:{match.group(2)}")
            for match in SchemaDetector.PG_DROP_INDEX.finditer(content):
                pg_hit = True
                sql_tags.add("POSTGRES_SCHEMA_CHANGE")
                sql_tags.add("POSTGRES_INDEX_CHANGE")
                sql_tags.add(f"POSTGRES_DROP_INDEX:{match.group(1)}")
            for match in SchemaDetector.PG_ADD_CONSTRAINT.finditer(content):
                pg_hit = True
                sql_tags.add("POSTGRES_SCHEMA_CHANGE")
                sql_tags.add("POSTGRES_CONSTRAINT_CHANGE")
                sql_tags.add(f"POSTGRES_ADD_CONSTRAINT:{match.group(1)}")
            for match in SchemaDetector.PG_DROP_CONSTRAINT.finditer(content):
                pg_hit = True
                sql_tags.add("POSTGRES_SCHEMA_CHANGE")
                sql_tags.add("POSTGRES_CONSTRAINT_CHANGE")
                sql_tags.add(f"POSTGRES_DROP_CONSTRAINT:{match.group(1)}")
            for match in SchemaDetector.PG_ADD_COLUMN.finditer(content):
                pg_hit = True
                sql_tags.add("POSTGRES_SCHEMA_CHANGE")
                sql_tags.add("POSTGRES_COLUMN_CHANGE")
                sql_tags.add(f"POSTGRES_ADD_COLUMN:{match.group(1)}")
            for match in SchemaDetector.PG_DROP_COLUMN.finditer(content):
                pg_hit = True
                sql_tags.add("POSTGRES_SCHEMA_CHANGE")
                sql_tags.add("POSTGRES_COLUMN_CHANGE")
                sql_tags.add(f"POSTGRES_DROP_COLUMN:{match.group(1)}")
            for match in SchemaDetector.PG_ALTER_COLUMN_TYPE.finditer(content):
                pg_hit = True
                sql_tags.add("POSTGRES_SCHEMA_CHANGE")
                sql_tags.add("POSTGRES_COLUMN_CHANGE")
                sql_tags.add(f"POSTGRES_ALTER_COLUMN_TYPE:{match.group(1)}")

            if pg_hit and "SQL_SCHEMA_CHANGE" not in sql_tags:
                sql_tags.add("SQL_SCHEMA_CHANGE")

            tags.extend(sorted(sql_tags))

        # Mongoose (JavaScript/TypeScript)
        elif ext in ['js', 'ts', 'jsx', 'tsx']:
            if SchemaDetector.MONGOOSE_SCHEMA.search(content):
                tags.append("MONGOOSE_SCHEMA")
            # Use findall to collect ALL mongoose.model('Name') calls; take the
            # LAST one which is always the canonical module.exports registration.
            # Using only .search() (first match) breaks files like Task.model.js
            # where the first call is an internal ref: mongoose.model('Project')
            # inside a pre-save hook, causing the wrong model name to be tagged.
            all_model_names = SchemaDetector.MONGOOSE_MODEL.findall(content)
            if all_model_names:
                # Last call = the real schema export (e.g. mongoose.model('Task', taskSchema))
                canonical_name = all_model_names[-1]
                tags.append(f"MONGOOSE_MODEL:{canonical_name}")

        # Django ORM (Python)
        elif ext == 'py' and SchemaDetector.DJANGO_MODEL.search(content):
            tags.append("DJANGO_MODEL")

        return tags
