# Phase 3: Security Enhancements Implementation Details

## Context & Overview

The current Snowflake MCP server lacks comprehensive security controls beyond basic Snowflake authentication. Production deployments require multiple layers of security including API authentication, SQL injection prevention, audit logging, and role-based access controls.

**Current Security Gaps:**
- No API authentication for HTTP/WebSocket endpoints
- Limited SQL injection prevention (only basic sqlglot parsing)
- No audit trail for queries and administrative actions
- Missing encryption validation for connections
- No role-based access controls for different client types
- Insufficient input validation and sanitization

**Target Architecture:**
- Multi-factor API authentication with API keys and JWT tokens
- Comprehensive SQL injection prevention with prepared statements
- Complete audit logging for security compliance
- Connection encryption validation and certificate management
- Role-based access controls with fine-grained permissions
- Input validation and sanitization at all entry points

## Dependencies Required

Add to `pyproject.toml`:
```toml
dependencies = [
    # Existing dependencies...
    "pyjwt>=2.8.0",              # JWT token handling
    "cryptography>=41.0.0",       # Already present, enhanced usage
    "bcrypt>=4.1.0",             # Password hashing
    "python-jose>=3.3.0",        # JWT utilities
    "passlib>=1.7.4",            # Password utilities
]

[project.optional-dependencies]
security = [
    "python-ldap>=3.4.0",       # LDAP integration
    "pyotp>=2.9.0",             # TOTP/MFA support
    "authlib>=1.2.1",           # OAuth2/OIDC support
]
```

## Implementation Plan

### 2. SQL Injection Prevention {#sql-injection}

**Step 1: Enhanced SQL Validation and Sanitization**

Create `snowflake_mcp_server/security/sql_security.py`:

```python
"""SQL injection prevention and query security."""

import re
import logging
from typing import Dict, Any, List, Optional, Set, Tuple
from dataclasses import dataclass
from enum import Enum
import sqlparse
from sqlparse import sql, tokens as T

logger = logging.getLogger(__name__)


class QueryRiskLevel(Enum):
    """Risk levels for SQL queries."""
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SecurityViolation:
    """SQL security violation."""
    violation_type: str
    risk_level: QueryRiskLevel
    description: str
    query_snippet: str
    position: Optional[int] = None


class SQLSecurityValidator:
    """Comprehensive SQL security validation."""
    
    def __init__(self):
        # Dangerous SQL patterns
        self.dangerous_patterns = {
            # Command injection attempts
            r';\s*(drop|delete|truncate|alter|create|insert|update)\s+': QueryRiskLevel.CRITICAL,
            r'(union\s+select|union\s+all\s+select)': QueryRiskLevel.HIGH,
            r'(exec|execute|sp_|xp_)\s*\(': QueryRiskLevel.CRITICAL,
            
            # Data exfiltration patterns
            r'(information_schema|sys\.|pg_|mysql\.)': QueryRiskLevel.MEDIUM,
            r'(load_file|into\s+outfile|into\s+dumpfile)': QueryRiskLevel.CRITICAL,
            
            # Comment-based injection
            r'(/\*.*?\*/|--|\#)': QueryRiskLevel.LOW,
            
            # String manipulation that could indicate injection
            r'(char\s*\(|ascii\s*\(|substring\s*\()': QueryRiskLevel.LOW,
            
            # Time-based attacks
            r'(waitfor\s+delay|sleep\s*\(|benchmark\s*\()': QueryRiskLevel.HIGH,
            
            # Boolean-based blind SQL injection
            r'(and\s+1=1|or\s+1=1|and\s+1=2|or\s+1=2)': QueryRiskLevel.MEDIUM,
        }
        
        # Allowed SQL keywords for read-only operations
        self.allowed_keywords = {
            'select', 'from', 'where', 'join', 'inner', 'left', 'right',
            'outer', 'on', 'as', 'order', 'by', 'group', 'having',
            'limit', 'offset', 'with', 'case', 'when', 'then', 'else',
            'end', 'and', 'or', 'not', 'in', 'exists', 'between',
            'like', 'ilike', 'is', 'null', 'distinct', 'all', 'any',
            'some', 'union', 'intersect', 'except', 'desc', 'asc',
            'show', 'describe', 'explain', 'cast', 'convert'
        }
        
        # Dangerous keywords that should never appear
        self.forbidden_keywords = {
            'drop', 'delete', 'insert', 'update', 'truncate', 'alter',
            'create', 'grant', 'revoke', 'exec', 'execute', 'call',
            'procedure', 'function', 'trigger', 'view', 'index',
            'database', 'schema', 'table', 'column'
        }
        
        # Snowflake-specific dangerous functions
        self.dangerous_functions = {
            'system$', 'get_ddl', 'current_role', 'current_user',
            'current_account', 'current_region'
        }
    
    def validate_query(self, query: str, context: Dict[str, Any] = None) -> List[SecurityViolation]:
        """
        Comprehensive SQL security validation.
        
        Returns list of security violations found.
        """
        violations = []
        
        # Normalize query
        normalized_query = self._normalize_query(query)
        
        # Check for dangerous patterns
        violations.extend(self._check_dangerous_patterns(normalized_query))
        
        # Parse and analyze SQL structure
        violations.extend(self._analyze_sql_structure(query))
        
        # Check for forbidden keywords
        violations.extend(self._check_forbidden_keywords(normalized_query))
        
        # Validate parameter usage
        violations.extend(self._check_parameter_safety(query, context or {}))
        
        # Check for complex injection techniques
        violations.extend(self._check_advanced_injection_patterns(normalized_query))
        
        return violations
    
    def is_query_safe(self, query: str, context: Dict[str, Any] = None) -> bool:
        """Check if query is safe to execute."""
        violations = self.validate_query(query, context)
        
        # Reject queries with HIGH or CRITICAL violations
        critical_violations = [
            v for v in violations 
            if v.risk_level in [QueryRiskLevel.HIGH, QueryRiskLevel.CRITICAL]
        ]
        
        return len(critical_violations) == 0
    
    def _normalize_query(self, query: str) -> str:
        """Normalize query for pattern matching."""
        # Remove extra whitespace
        normalized = re.sub(r'\s+', ' ', query.strip().lower())
        
        # Remove string literals to avoid false positives
        normalized = re.sub(r"'[^']*'", "'STRING'", normalized)
        normalized = re.sub(r'"[^"]*"', '"STRING"', normalized)
        
        return normalized
    
    def _check_dangerous_patterns(self, normalized_query: str) -> List[SecurityViolation]:
        """Check for known dangerous SQL patterns."""
        violations = []
        
        for pattern, risk_level in self.dangerous_patterns.items():
            matches = re.finditer(pattern, normalized_query, re.IGNORECASE)
            
            for match in matches:
                violations.append(SecurityViolation(
                    violation_type="dangerous_pattern",
                    risk_level=risk_level,
                    description=f"Detected dangerous SQL pattern: {pattern}",
                    query_snippet=match.group(),
                    position=match.start()
                ))
        
        return violations
    
    def _analyze_sql_structure(self, query: str) -> List[SecurityViolation]:
        """Analyze SQL structure using sqlparse."""
        violations = []
        
        try:
            parsed = sqlparse.parse(query)
            
            for statement in parsed:
                violations.extend(self._analyze_statement(statement))
                
        except Exception as e:
            # If parsing fails, it might be malformed SQL
            violations.append(SecurityViolation(
                violation_type="parse_error",
                risk_level=QueryRiskLevel.MEDIUM,
                description=f"Failed to parse SQL: {str(e)}",
                query_snippet=query[:100]
            ))
        
        return violations
    
    def _analyze_statement(self, statement: sql.Statement) -> List[SecurityViolation]:
        """Analyze individual SQL statement."""
        violations = []
        
        # Check statement type
        first_token = statement.token_first(skip_ws=True, skip_cm=True)
        if first_token and first_token.ttype in (T.Keyword, T.Keyword.DML):
            keyword = first_token.value.upper()
            
            if keyword in self.forbidden_keywords:
                violations.append(SecurityViolation(
                    violation_type="forbidden_keyword",
                    risk_level=QueryRiskLevel.CRITICAL,
                    description=f"Forbidden SQL keyword: {keyword}",
                    query_snippet=keyword
                ))
        
        # Recursively check tokens
        for token in statement.flatten():
            if token.ttype is T.Keyword and token.value.upper() in self.forbidden_keywords:
                violations.append(SecurityViolation(
                    violation_type="forbidden_keyword",
                    risk_level=QueryRiskLevel.HIGH,
                    description=f"Forbidden keyword in query: {token.value}",
                    query_snippet=token.value
                ))
        
        return violations
    
    def _check_forbidden_keywords(self, normalized_query: str) -> List[SecurityViolation]:
        """Check for forbidden SQL keywords."""
        violations = []
        
        words = re.findall(r'\b\w+\b', normalized_query)
        
        for word in words:
            if word.lower() in self.forbidden_keywords:
                violations.append(SecurityViolation(
                    violation_type="forbidden_keyword",
                    risk_level=QueryRiskLevel.HIGH,
                    description=f"Forbidden keyword detected: {word}",
                    query_snippet=word
                ))
        
        return violations
    
    def _check_parameter_safety(self, query: str, context: Dict[str, Any]) -> List[SecurityViolation]:
        """Check for unsafe parameter usage."""
        violations = []
        
        # Look for potential SQL injection in parameters
        param_pattern = r'\{[^}]+\}'  # Simple parameter pattern
        
        for match in re.finditer(param_pattern, query):
            param_name = match.group()[1:-1]  # Remove braces
            
            if param_name in context:
                param_value = str(context[param_name])
                
                # Check if parameter value contains SQL keywords
                if any(keyword in param_value.lower() for keyword in self.forbidden_keywords):
                    violations.append(SecurityViolation(
                        violation_type="unsafe_parameter",
                        risk_level=QueryRiskLevel.HIGH,
                        description=f"Parameter {param_name} contains SQL keywords",
                        query_snippet=param_value[:50]
                    ))
        
        return violations
    
    def _check_advanced_injection_patterns(self, normalized_query: str) -> List[SecurityViolation]:
        """Check for advanced SQL injection techniques."""
        violations = []
        
        # Check for encoding-based injection attempts
        encoding_patterns = [
            r'char\s*\(\s*\d+\s*\)',  # CHAR() function abuse
            r'0x[0-9a-f]+',  # Hexadecimal encoding
            r'%[0-9a-f]{2}',  # URL encoding
        ]
        
        for pattern in encoding_patterns:
            if re.search(pattern, normalized_query, re.IGNORECASE):
                violations.append(SecurityViolation(
                    violation_type="encoding_injection",
                    risk_level=QueryRiskLevel.MEDIUM,
                    description=f"Potential encoding-based injection: {pattern}",
                    query_snippet=pattern
                ))
        
        # Check for stacked queries
        if ';' in normalized_query and normalized_query.count(';') > 1:
            violations.append(SecurityViolation(
                violation_type="stacked_queries",
                risk_level=QueryRiskLevel.HIGH,
                description="Multiple statements detected (stacked queries)",
                query_snippet="Multiple semicolons"
            ))
        
        return violations


class QuerySanitizer:
    """Sanitize SQL queries for safe execution."""
    
    def __init__(self):
        self.validator = SQLSecurityValidator()
    
    def sanitize_query(self, query: str, max_length: int = 10000) -> str:
        """Sanitize SQL query."""
        
        # Length check
        if len(query) > max_length:
            raise ValueError(f"Query too long: {len(query)} > {max_length}")
        
        # Remove dangerous characters and normalize
        sanitized = query.strip()
        
        # Remove comments
        sanitized = re.sub(r'--.*?\n', '\n', sanitized)
        sanitized = re.sub(r'/\*.*?\*/', ' ', sanitized, flags=re.DOTALL)
        
        # Normalize whitespace
        sanitized = re.sub(r'\s+', ' ', sanitized).strip()
        
        # Validate sanitized query
        violations = self.validator.validate_query(sanitized)
        critical_violations = [
            v for v in violations 
            if v.risk_level in [QueryRiskLevel.HIGH, QueryRiskLevel.CRITICAL]
        ]
        
        if critical_violations:
            violation_details = "; ".join([v.description for v in critical_violations])
            raise ValueError(f"Query contains security violations: {violation_details}")
        
        return sanitized
    
    def prepare_query(self, query_template: str, parameters: Dict[str, Any]) -> str:
        """Prepare parameterized query safely."""
        
        # Validate template
        violations = self.validator.validate_query(query_template)
        if any(v.risk_level == QueryRiskLevel.CRITICAL for v in violations):
            raise ValueError("Query template contains critical security violations")
        
        # Sanitize parameters
        sanitized_params = {}
        for key, value in parameters.items():
            sanitized_params[key] = self._sanitize_parameter(value)
        
        # Format query with sanitized parameters
        try:
            formatted_query = query_template.format(**sanitized_params)
        except KeyError as e:
            raise ValueError(f"Missing parameter: {e}")
        except Exception as e:
            raise ValueError(f"Error formatting query: {e}")
        
        # Final validation
        return self.sanitize_query(formatted_query)
    
    def _sanitize_parameter(self, value: Any) -> str:
        """Sanitize individual parameter value."""
        
        if value is None:
            return "NULL"
        
        # Convert to string
        str_value = str(value)
        
        # Check for SQL injection attempts in parameter
        violations = self.validator.validate_query(str_value)
        if any(v.risk_level == QueryRiskLevel.CRITICAL for v in violations):
            raise ValueError(f"Parameter contains SQL injection attempt: {str_value}")
        
        # Escape single quotes
        escaped = str_value.replace("'", "''")
        
        # For string parameters, wrap in quotes
        if isinstance(value, str):
            return f"'{escaped}'"
        
        return escaped


# Global instances
sql_validator = SQLSecurityValidator()
query_sanitizer = QuerySanitizer()


# Decorator for SQL security validation
def validate_sql_security(func):
    """Decorator to validate SQL queries for security."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Extract query from arguments
        query = None
        if 'query' in kwargs:
            query = kwargs['query']
        elif len(args) > 0 and isinstance(args[0], str):
            query = args[0]
        
        if query:
            if not sql_validator.is_query_safe(query):
                violations = sql_validator.validate_query(query)
                critical_violations = [
                    v.description for v in violations
                    if v.risk_level in [QueryRiskLevel.HIGH, QueryRiskLevel.CRITICAL]
                ]
                raise ValueError(f"SQL security violations: {'; '.join(critical_violations)}")
        
        return await func(*args, **kwargs)
    
    return wrapper
```

