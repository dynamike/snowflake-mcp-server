"""SQL injection prevention and query validation."""

import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import TokenType, parse, tokens
from sqlglot.expressions import Expression

from ..config import get_config
from ..monitoring import get_audit_logger, get_structured_logger

logger = logging.getLogger(__name__)


class SQLInjectionRisk(Enum):
    """SQL injection risk levels."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class QueryType(Enum):
    """Types of SQL queries."""
    SELECT = "select"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    CREATE = "create"
    DROP = "drop"
    ALTER = "alter"
    TRUNCATE = "truncate"
    GRANT = "grant"
    REVOKE = "revoke"
    EXECUTE = "execute"
    CALL = "call"
    UNKNOWN = "unknown"


@dataclass
class SQLValidationResult:
    """Result of SQL validation."""
    
    is_valid: bool
    risk_level: SQLInjectionRisk
    query_type: QueryType
    violations: List[str]
    sanitized_query: Optional[str] = None
    blocked_patterns: List[str] = None
    allowed_operations: List[str] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.blocked_patterns is None:
            self.blocked_patterns = []
        if self.allowed_operations is None:
            self.allowed_operations = []
        if self.metadata is None:
            self.metadata = {}


class SQLInjectionError(Exception):
    """Raised when SQL injection is detected or query is invalid."""
    
    def __init__(self, message: str, risk_level: SQLInjectionRisk, 
                 violations: List[str], query: str):
        super().__init__(message)
        self.risk_level = risk_level
        self.violations = violations
        self.query = query


class SQLPatternMatcher:
    """Matches dangerous SQL patterns that could indicate injection attempts."""
    
    def __init__(self):
        # Critical patterns that should always be blocked
        self.critical_patterns = [
            # Union-based injection
            r'union\s+(?:all\s+)?select',
            r'union\s+(?:distinct\s+)?select',
            
            # Boolean-based blind injection
            r'(?:and|or)\s+\d+\s*[=<>]\s*\d+',
            r'(?:and|or)\s+[\'"]\w+[\'"]?\s*[=<>]\s*[\'"]\w+[\'"]?',
            
            # Time-based blind injection
            r'waitfor\s+delay',
            r'sleep\s*\(',
            r'pg_sleep\s*\(',
            r'benchmark\s*\(',
            
            # Stacked queries
            r';\s*(?:insert|update|delete|drop|create|alter|grant|revoke)',
            
            # Information schema access
            r'information_schema\.',
            r'sys\.',
            r'mysql\.',
            
            # Command execution
            r'xp_cmdshell',
            r'sp_execute',
            r'exec\s*\(',
            r'execute\s*\(',
            
            # File operations
            r'load_file\s*\(',
            r'into\s+outfile',
            r'into\s+dumpfile',
        ]
        
        # High-risk patterns
        self.high_risk_patterns = [
            # Comment injection
            r'(?:--|#|/\*)',
            
            # Hex/char encoding
            r'0x[0-9a-fA-F]+',
            r'char\s*\(',
            r'chr\s*\(',
            r'ascii\s*\(',
            
            # Concatenation functions
            r'concat\s*\(',
            r'group_concat\s*\(',
            
            # Database fingerprinting
            r'@@version',
            r'@@global',
            r'version\s*\(',
            r'user\s*\(',
            r'database\s*\(',
            r'schema\s*\(',
        ]
        
        # Medium-risk patterns
        self.medium_risk_patterns = [
            # Single quotes handling
            r"'[^']*'[^']*'",
            
            # Multiple conditions
            r'(?:and|or)\s+[\w\s]*(?:=|<>|!=|like)',
            
            # Subqueries
            r'\(\s*select\s+',
            
            # Case statements
            r'case\s+when',
            
            # Casting
            r'cast\s*\(',
            r'convert\s*\(',
        ]
        
        # Low-risk patterns (suspicious but might be legitimate)
        self.low_risk_patterns = [
            # Multiple operators
            r'[=<>!]{2,}',
            
            # Unusual spacing
            r'\s{5,}',
            
            # Special characters
            r'[%_*]{3,}',
        ]
        
        # Compile patterns for performance
        self._compile_patterns()
    
    def _compile_patterns(self):
        """Compile regex patterns for better performance."""
        self.compiled_critical = [re.compile(p, re.IGNORECASE) for p in self.critical_patterns]
        self.compiled_high = [re.compile(p, re.IGNORECASE) for p in self.high_risk_patterns]
        self.compiled_medium = [re.compile(p, re.IGNORECASE) for p in self.medium_risk_patterns]
        self.compiled_low = [re.compile(p, re.IGNORECASE) for p in self.low_risk_patterns]
    
    def analyze_query(self, query: str) -> Tuple[SQLInjectionRisk, List[str]]:
        """Analyze query for injection patterns."""
        violations = []
        max_risk = SQLInjectionRisk.NONE
        
        # Check critical patterns
        for pattern in self.compiled_critical:
            if pattern.search(query):
                violations.append(f"Critical pattern detected: {pattern.pattern}")
                max_risk = SQLInjectionRisk.CRITICAL
        
        # Check high-risk patterns
        if max_risk != SQLInjectionRisk.CRITICAL:
            for pattern in self.compiled_high:
                if pattern.search(query):
                    violations.append(f"High-risk pattern detected: {pattern.pattern}")
                    max_risk = SQLInjectionRisk.HIGH
        
        # Check medium-risk patterns
        if max_risk not in [SQLInjectionRisk.CRITICAL, SQLInjectionRisk.HIGH]:
            for pattern in self.compiled_medium:
                if pattern.search(query):
                    violations.append(f"Medium-risk pattern detected: {pattern.pattern}")
                    max_risk = SQLInjectionRisk.MEDIUM
        
        # Check low-risk patterns
        if max_risk == SQLInjectionRisk.NONE:
            for pattern in self.compiled_low:
                if pattern.search(query):
                    violations.append(f"Low-risk pattern detected: {pattern.pattern}")
                    max_risk = SQLInjectionRisk.LOW
        
        return max_risk, violations


class SQLTokenAnalyzer:
    """Analyzes SQL tokens for suspicious patterns."""
    
    def __init__(self):
        self.suspicious_token_sequences = [
            # UNION injection patterns
            [TokenType.UNION, TokenType.SELECT],
            [TokenType.UNION, TokenType.ALL, TokenType.SELECT],
            
            # Boolean injection patterns
            [TokenType.AND, TokenType.NUMBER, TokenType.EQ, TokenType.NUMBER],
            [TokenType.OR, TokenType.NUMBER, TokenType.EQ, TokenType.NUMBER],
            
            # Comment patterns
            [TokenType.COMMENT],
            [TokenType.BLOCK_COMMENT],
        ]
        
        # Tokens that should not appear in read-only queries
        self.forbidden_tokens_readonly = {
            TokenType.INSERT, TokenType.UPDATE, TokenType.DELETE,
            TokenType.DROP, TokenType.CREATE, TokenType.ALTER,
            TokenType.TRUNCATE, TokenType.GRANT, TokenType.REVOKE,
            TokenType.EXECUTE
        }
    
    def analyze_tokens(self, query: str, readonly_mode: bool = True) -> Tuple[List[str], QueryType]:
        """Analyze SQL tokens for suspicious patterns."""
        violations = []
        query_type = QueryType.UNKNOWN
        
        try:
            # Tokenize the query
            token_list = list(tokens.Tokenizer().tokenize(query))
            
            if not token_list:
                return ["Empty query"], QueryType.UNKNOWN
            
            # Determine query type from first meaningful token
            for token in token_list:
                if token.token_type in [
                    TokenType.SELECT, TokenType.INSERT, TokenType.UPDATE,
                    TokenType.DELETE, TokenType.CREATE, TokenType.DROP,
                    TokenType.ALTER, TokenType.TRUNCATE, TokenType.GRANT,
                    TokenType.REVOKE, TokenType.EXECUTE, TokenType.CALL
                ]:
                    query_type = QueryType(token.token_type.name.lower())
                    break
            
            # Check for forbidden tokens in readonly mode
            if readonly_mode:
                for token in token_list:
                    if token.token_type in self.forbidden_tokens_readonly:
                        violations.append(f"Forbidden operation in readonly mode: {token.token_type.name}")
            
            # Check for suspicious token sequences
            for i in range(len(token_list) - 1):
                for suspicious_sequence in self.suspicious_token_sequences:
                    if self._matches_sequence(token_list[i:], suspicious_sequence):
                        violations.append(f"Suspicious token sequence detected: {[t.name for t in suspicious_sequence]}")
            
            # Check for excessive comments
            comment_count = sum(1 for token in token_list 
                              if token.token_type in [TokenType.COMMENT, TokenType.BLOCK_COMMENT])
            if comment_count > 3:
                violations.append(f"Excessive comments detected: {comment_count}")
            
            # Check for unusual string patterns
            string_tokens = [token for token in token_list if token.token_type == TokenType.STRING]
            for token in string_tokens:
                if self._is_suspicious_string(token.text):
                    violations.append(f"Suspicious string literal: {token.text[:50]}...")
            
        except Exception as e:
            violations.append(f"Token analysis failed: {str(e)}")
        
        return violations, query_type
    
    def _matches_sequence(self, tokens: List, pattern: List[TokenType]) -> bool:
        """Check if token sequence matches a suspicious pattern."""
        if len(tokens) < len(pattern):
            return False
        
        for i, expected_type in enumerate(pattern):
            if i >= len(tokens) or tokens[i].token_type != expected_type:
                return False
        
        return True
    
    def _is_suspicious_string(self, text: str) -> bool:
        """Check if a string literal looks suspicious."""
        # Remove quotes
        content = text.strip("'\"")
        
        # Check for SQL keywords in strings
        sql_keywords = ['select', 'union', 'insert', 'update', 'delete', 'drop', 'exec']
        if any(keyword in content.lower() for keyword in sql_keywords):
            return True
        
        # Check for unusual characters
        if re.search(r'[;\x00-\x1f\x7f-\xff]', content):
            return True
        
        # Check for encoded content
        if re.search(r'(&#x?[0-9a-f]+;|%[0-9a-f]{2}|\\x[0-9a-f]{2})', content, re.IGNORECASE):
            return True
        
        return False


class SQLStructureValidator:
    """Validates SQL query structure using AST parsing."""
    
    def __init__(self):
        self.allowed_functions = {
            # String functions
            'upper', 'lower', 'trim', 'length', 'substr', 'substring',
            'replace', 'regexp_replace', 'split_part',
            
            # Numeric functions
            'abs', 'ceil', 'floor', 'round', 'trunc', 'mod',
            'power', 'sqrt', 'exp', 'ln', 'log',
            
            # Date functions
            'current_date', 'current_time', 'current_timestamp',
            'date_trunc', 'date_part', 'extract', 'dateadd', 'datediff',
            
            # Aggregate functions
            'count', 'sum', 'avg', 'min', 'max', 'stddev', 'variance',
            
            # Window functions
            'row_number', 'rank', 'dense_rank', 'lead', 'lag',
            'first_value', 'last_value',
            
            # Conditional functions
            'case', 'when', 'then', 'else', 'end', 'coalesce', 'nullif',
            'greatest', 'least',
            
            # Type conversion
            'cast', 'try_cast', 'to_char', 'to_date', 'to_number',
        }
        
        self.forbidden_functions = {
            # System functions
            'system', 'exec', 'execute', 'xp_cmdshell', 'sp_execute',
            
            # File functions
            'load_file', 'into_outfile', 'into_dumpfile',
            
            # Information functions
            'user', 'current_user', 'session_user', 'version',
            'database', 'schema', 'connection_id',
            
            # Admin functions
            'kill', 'shutdown', 'create_user', 'drop_user',
        }
    
    def validate_structure(self, query: str) -> List[str]:
        """Validate SQL query structure."""
        violations = []
        
        try:
            # Parse the query
            parsed = parse(query, dialect="snowflake")
            
            if not parsed:
                violations.append("Failed to parse query")
                return violations
            
            # Validate each statement
            for statement in parsed:
                violations.extend(self._validate_statement(statement))
        
        except Exception as e:
            violations.append(f"Structure validation failed: {str(e)}")
        
        return violations
    
    def _validate_statement(self, statement: Expression) -> List[str]:
        """Validate a single SQL statement."""
        violations = []
        
        # Check for forbidden functions
        functions = self._extract_functions(statement)
        for func_name in functions:
            if func_name.lower() in self.forbidden_functions:
                violations.append(f"Forbidden function: {func_name}")
        
        # Check for nested queries depth
        max_depth = self._get_max_nesting_depth(statement)
        if max_depth > 5:
            violations.append(f"Query nesting too deep: {max_depth} levels")
        
        # Check for excessive complexity
        complexity = self._calculate_complexity(statement)
        if complexity > 100:
            violations.append(f"Query too complex: complexity score {complexity}")
        
        return violations
    
    def _extract_functions(self, expression: Expression) -> Set[str]:
        """Extract all function names from an expression."""
        functions = set()
        
        def visit(node):
            if hasattr(node, 'this') and hasattr(node.this, 'name'):
                if node.__class__.__name__ in ['Anonymous', 'Function']:
                    functions.add(node.this.name)
            
            for child in node.iter_child_nodes() if hasattr(node, 'iter_child_nodes') else []:
                visit(child)
        
        try:
            visit(expression)
        except Exception:
            pass  # Ignore errors in traversal
        
        return functions
    
    def _get_max_nesting_depth(self, expression: Expression, current_depth: int = 0) -> int:
        """Calculate maximum nesting depth of subqueries."""
        max_depth = current_depth
        
        try:
            for child in expression.iter_child_nodes() if hasattr(expression, 'iter_child_nodes') else []:
                if child.__class__.__name__ in ['Select', 'Subquery']:
                    child_depth = self._get_max_nesting_depth(child, current_depth + 1)
                    max_depth = max(max_depth, child_depth)
                else:
                    child_depth = self._get_max_nesting_depth(child, current_depth)
                    max_depth = max(max_depth, child_depth)
        except Exception:
            pass  # Ignore errors in traversal
        
        return max_depth
    
    def _calculate_complexity(self, expression: Expression) -> int:
        """Calculate query complexity score."""
        complexity = 0
        
        try:
            # Count various elements that add complexity
            for child in expression.iter_child_nodes() if hasattr(expression, 'iter_child_nodes') else []:
                class_name = child.__class__.__name__
                
                if class_name in ['Select', 'Subquery']:
                    complexity += 10
                elif class_name in ['Join', 'LeftJoin', 'RightJoin', 'FullJoin']:
                    complexity += 5
                elif class_name in ['Where', 'Having']:
                    complexity += 3
                elif class_name in ['OrderBy', 'GroupBy']:
                    complexity += 2
                elif class_name in ['Function', 'Anonymous']:
                    complexity += 1
                
                # Recursively calculate complexity
                complexity += self._calculate_complexity(child)
        except Exception:
            pass  # Ignore errors in traversal
        
        return complexity


class SQLValidator:
    """Main SQL validation and injection prevention system."""
    
    def __init__(self):
        self.config = get_config()
        self.logger = get_structured_logger().get_logger("sql_validator")
        self.audit_logger = get_audit_logger()
        
        # Initialize components
        self.pattern_matcher = SQLPatternMatcher()
        self.token_analyzer = SQLTokenAnalyzer()
        self.structure_validator = SQLStructureValidator()
        
        # Configuration
        self.max_query_length = getattr(self.config.security, 'max_query_length', 10000)
        self.readonly_mode = getattr(self.config.security, 'readonly_mode', True)
        self.strict_validation = getattr(self.config.security, 'strict_sql_validation', True)
        self.blocked_risk_levels = {
            SQLInjectionRisk.CRITICAL,
            SQLInjectionRisk.HIGH,
        }
        
        if self.strict_validation:
            self.blocked_risk_levels.add(SQLInjectionRisk.MEDIUM)
    
    def validate_query(self, query: str, user_id: str = "unknown", 
                      client_ip: str = "unknown") -> SQLValidationResult:
        """Validate a SQL query for injection attempts and policy compliance."""
        start_time = time.time()
        violations = []
        risk_level = SQLInjectionRisk.NONE
        query_type = QueryType.UNKNOWN
        
        try:
            # Basic validation
            if not query or not query.strip():
                violations.append("Empty query")
                risk_level = SQLInjectionRisk.HIGH
            
            # Length check
            if len(query) > self.max_query_length:
                violations.append(f"Query too long: {len(query)} characters")
                risk_level = SQLInjectionRisk.MEDIUM
            
            # Pattern matching analysis
            if not violations:
                pattern_risk, pattern_violations = self.pattern_matcher.analyze_query(query)
                violations.extend(pattern_violations)
                risk_level = max(risk_level, pattern_risk, key=lambda x: list(SQLInjectionRisk).index(x))
            
            # Token analysis
            if not violations or risk_level not in self.blocked_risk_levels:
                token_violations, query_type = self.token_analyzer.analyze_tokens(
                    query, self.readonly_mode
                )
                violations.extend(token_violations)
                if token_violations:
                    risk_level = max(risk_level, SQLInjectionRisk.MEDIUM, key=lambda x: list(SQLInjectionRisk).index(x))
            
            # Structure validation
            if not violations or risk_level not in self.blocked_risk_levels:
                structure_violations = self.structure_validator.validate_structure(query)
                violations.extend(structure_violations)
                if structure_violations:
                    risk_level = max(risk_level, SQLInjectionRisk.MEDIUM, key=lambda x: list(SQLInjectionRisk).index(x))
            
            # Determine if query should be blocked
            is_valid = risk_level not in self.blocked_risk_levels
            
            # Create result
            result = SQLValidationResult(
                is_valid=is_valid,
                risk_level=risk_level,
                query_type=query_type,
                violations=violations,
                metadata={
                    "query_length": len(query),
                    "validation_time": time.time() - start_time,
                    "readonly_mode": self.readonly_mode,
                    "strict_validation": self.strict_validation,
                }
            )
            
            # Log validation result
            self.logger.info(
                "SQL validation completed",
                user_id=user_id,
                client_ip=client_ip,
                query_type=query_type.value,
                risk_level=risk_level.value,
                is_valid=is_valid,
                violation_count=len(violations),
                query_length=len(query),
                event_type="sql_validation"
            )
            
            # Audit log for blocked queries
            if not is_valid:
                self.audit_logger.log_authorization(
                    user_id=user_id,
                    resource="sql_query",
                    action="execute",
                    granted=False,
                    reason=f"SQL validation failed: {risk_level.value} risk, {len(violations)} violations"
                )
                
                self.logger.warning(
                    "Blocked potentially malicious SQL query",
                    user_id=user_id,
                    client_ip=client_ip,
                    risk_level=risk_level.value,
                    violations=violations,
                    query_preview=query[:200] + "..." if len(query) > 200 else query,
                    event_type="sql_injection_blocked"
                )
            
            return result
            
        except Exception as e:
            # If validation fails, err on the side of caution
            error_msg = f"SQL validation error: {str(e)}"
            violations.append(error_msg)
            
            self.logger.error(
                "SQL validation failed",
                user_id=user_id,
                error=str(e),
                query_length=len(query) if query else 0,
                event_type="sql_validation_error"
            )
            
            return SQLValidationResult(
                is_valid=False,
                risk_level=SQLInjectionRisk.HIGH,
                query_type=QueryType.UNKNOWN,
                violations=violations,
                metadata={"validation_error": str(e)}
            )
    
    def sanitize_query(self, query: str) -> str:
        """Attempt to sanitize a SQL query (basic implementation)."""
        if not query:
            return query
        
        # Remove comments
        query = re.sub(r'--[^\n]*', '', query)
        query = re.sub(r'/\*.*?\*/', '', query, flags=re.DOTALL)
        
        # Normalize whitespace
        query = re.sub(r'\s+', ' ', query).strip()
        
        # Remove trailing semicolons (prevent stacked queries)
        query = query.rstrip(';')
        
        return query
    
    def get_validation_stats(self) -> Dict[str, Any]:
        """Get validation statistics."""
        # This would typically track statistics over time
        # For now, return basic configuration info
        return {
            "configuration": {
                "max_query_length": self.max_query_length,
                "readonly_mode": self.readonly_mode,
                "strict_validation": self.strict_validation,
                "blocked_risk_levels": [level.value for level in self.blocked_risk_levels],
            },
            "pattern_counts": {
                "critical_patterns": len(self.pattern_matcher.critical_patterns),
                "high_risk_patterns": len(self.pattern_matcher.high_risk_patterns),
                "medium_risk_patterns": len(self.pattern_matcher.medium_risk_patterns),
                "low_risk_patterns": len(self.pattern_matcher.low_risk_patterns),
            },
            "allowed_functions": len(self.structure_validator.allowed_functions),
            "forbidden_functions": len(self.structure_validator.forbidden_functions),
        }


# Global SQL validator instance
_sql_validator: Optional[SQLValidator] = None


def get_sql_validator() -> SQLValidator:
    """Get the global SQL validator instance."""
    global _sql_validator
    if _sql_validator is None:
        _sql_validator = SQLValidator()
    return _sql_validator


def validate_sql_query(query: str, user_id: str = "unknown", 
                      client_ip: str = "unknown") -> SQLValidationResult:
    """Validate a SQL query for injection attempts."""
    validator = get_sql_validator()
    return validator.validate_query(query, user_id, client_ip)


def require_sql_validation(strict: bool = True):
    """Decorator to validate SQL queries in function arguments."""
    def decorator(func):
        from functools import wraps
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Look for SQL query in arguments
            query = kwargs.get('query') or kwargs.get('sql')
            if not query and args:
                # Try to find query in positional arguments
                for arg in args:
                    if isinstance(arg, str) and len(arg) > 10 and any(
                        keyword in arg.lower() for keyword in ['select', 'insert', 'update', 'delete']
                    ):
                        query = arg
                        break
            
            if query:
                user_id = kwargs.get('user_id', 'unknown')
                client_ip = kwargs.get('client_ip', 'unknown')
                
                validator = get_sql_validator()
                result = validator.validate_query(query, user_id, client_ip)
                
                if not result.is_valid:
                    raise SQLInjectionError(
                        f"SQL validation failed: {result.risk_level.value} risk detected",
                        result.risk_level,
                        result.violations,
                        query
                    )
                
                # Add validation result to kwargs
                kwargs['sql_validation_result'] = result
            
            return await func(*args, **kwargs)
        
        return wrapper
    
    return decorator


# FastAPI endpoints for SQL validation
async def validate_sql_endpoint(query: str, user_id: str = "api") -> Dict[str, Any]:
    """API endpoint to validate SQL queries."""
    validator = get_sql_validator()
    result = validator.validate_query(query, user_id)
    
    return {
        "is_valid": result.is_valid,
        "risk_level": result.risk_level.value,
        "query_type": result.query_type.value,
        "violations": result.violations,
        "metadata": result.metadata,
    }


async def get_sql_validation_stats_endpoint() -> Dict[str, Any]:
    """API endpoint to get SQL validation statistics."""
    validator = get_sql_validator()
    return validator.get_validation_stats()


if __name__ == "__main__":
    # Test SQL validation
    validator = SQLValidator()
    
    test_queries = [
        "SELECT * FROM users WHERE id = 1",  # Safe query
        "SELECT * FROM users WHERE id = 1 UNION SELECT * FROM passwords",  # SQL injection
        "SELECT * FROM users; DROP TABLE users; --",  # Stacked query injection
        "SELECT * FROM users WHERE name = 'admin' OR '1'='1'",  # Boolean injection
        "SELECT SLEEP(5)",  # Time-based injection
    ]
    
    for query in test_queries:
        result = validator.validate_query(query, "test_user")
        print(f"\nQuery: {query[:50]}...")
        print(f"Valid: {result.is_valid}")
        print(f"Risk: {result.risk_level.value}")
        print(f"Type: {result.query_type.value}")
        if result.violations:
            print(f"Violations: {result.violations}")