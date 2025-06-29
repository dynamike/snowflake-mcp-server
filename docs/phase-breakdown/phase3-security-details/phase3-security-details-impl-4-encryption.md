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

### 4. Connection Encryption Validation {#encryption}

**Step 1: TLS and Certificate Validation**

Create `snowflake_mcp_server/security/encryption.py`:

```python
"""Connection encryption and certificate validation."""

import ssl
import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from dataclasses import dataclass
import socket
import OpenSSL.crypto
from cryptography import x509
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)


@dataclass
class CertificateInfo:
    """SSL certificate information."""
    subject: str
    issuer: str
    serial_number: str
    not_before: datetime
    not_after: datetime
    signature_algorithm: str
    key_size: int
    san_domains: List[str]
    is_valid: bool
    is_expired: bool
    days_until_expiry: int


class EncryptionValidator:
    """Validate encryption and certificate security."""
    
    def __init__(self):
        self.min_tls_version = ssl.TLSVersion.TLSv1_2
        self.required_ciphers = [
            'ECDHE-RSA-AES256-GCM-SHA384',
            'ECDHE-RSA-AES128-GCM-SHA256',
            'ECDHE-RSA-AES256-SHA384',
            'ECDHE-RSA-AES128-SHA256'
        ]
        self.weak_ciphers = [
            'RC4', 'DES', 'MD5', 'SHA1', 'NULL'
        ]
    
    async def validate_snowflake_connection(self, account: str) -> Dict[str, Any]:
        """Validate Snowflake connection encryption."""
        
        # Construct Snowflake hostname
        hostname = f"{account}.snowflakecomputing.com"
        port = 443
        
        try:
            # Create SSL context with strong settings
            context = ssl.create_default_context()
            context.minimum_version = self.min_tls_version
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            
            # Connect and get certificate info
            with socket.create_connection((hostname, port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    
                    # Get certificate
                    cert_der = ssock.getpeercert(binary_form=True)
                    cert_info = self._parse_certificate(cert_der)
                    
                    # Get connection info
                    cipher = ssock.cipher()
                    protocol = ssock.version()
                    
                    return {
                        "hostname": hostname,
                        "port": port,
                        "certificate": cert_info,
                        "tls_version": protocol,
                        "cipher_suite": cipher[0] if cipher else None,
                        "cipher_strength": cipher[2] if cipher else None,
                        "is_secure": self._evaluate_connection_security(cert_info, cipher, protocol)
                    }
        
        except Exception as e:
            logger.error(f"Failed to validate Snowflake connection encryption: {e}")
            return {
                "hostname": hostname,
                "port": port,
                "error": str(e),
                "is_secure": False
            }
    
    def _parse_certificate(self, cert_der: bytes) -> CertificateInfo:
        """Parse SSL certificate and extract information."""
        
        try:
            cert = x509.load_der_x509_certificate(cert_der, default_backend())
            
            # Extract subject and issuer
            subject = cert.subject.rfc4514_string()
            issuer = cert.issuer.rfc4514_string()
            
            # Extract SAN domains
            san_domains = []
            try:
                san_ext = cert.extensions.get_extension_for_oid(x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
                san_domains = [name.value for name in san_ext.value]
            except x509.ExtensionNotFound:
                pass
            
            # Calculate days until expiry
            days_until_expiry = (cert.not_valid_after - datetime.now()).days
            
            return CertificateInfo(
                subject=subject,
                issuer=issuer,
                serial_number=str(cert.serial_number),
                not_before=cert.not_valid_before,
                not_after=cert.not_valid_after,
                signature_algorithm=cert.signature_algorithm_oid._name,
                key_size=cert.public_key().key_size,
                san_domains=san_domains,
                is_valid=cert.not_valid_before <= datetime.now() <= cert.not_valid_after,
                is_expired=datetime.now() > cert.not_valid_after,
                days_until_expiry=days_until_expiry
            )
        
        except Exception as e:
            logger.error(f"Failed to parse certificate: {e}")
            raise
    
    def _evaluate_connection_security(
        self,
        cert_info: CertificateInfo,
        cipher: tuple,
        protocol: str
    ) -> bool:
        """Evaluate overall connection security."""
        
        security_checks = []
        
        # Certificate validity
        security_checks.append(cert_info.is_valid and not cert_info.is_expired)
        
        # Certificate expiry warning (less than 30 days)
        if cert_info.days_until_expiry < 30:
            logger.warning(f"Certificate expires in {cert_info.days_until_expiry} days")
        
        # TLS version check
        security_checks.append(protocol in ['TLSv1.2', 'TLSv1.3'])
        
        # Cipher strength check
        if cipher:
            cipher_name = cipher[0]
            security_checks.append(not any(weak in cipher_name for weak in self.weak_ciphers))
            security_checks.append(cipher[2] >= 128)  # Key size >= 128 bits
        
        # Key size check
        security_checks.append(cert_info.key_size >= 2048)
        
        return all(security_checks)
    
    async def validate_client_certificate(self, cert_pem: str) -> Dict[str, Any]:
        """Validate client certificate for mutual TLS."""
        
        try:
            cert = x509.load_pem_x509_certificate(cert_pem.encode(), default_backend())
            cert_info = self._parse_certificate(cert.public_bytes(x509.Encoding.DER))
            
            # Additional client certificate checks
            validation_results = {
                "certificate_info": cert_info,
                "is_valid": cert_info.is_valid,
                "is_expired": cert_info.is_expired,
                "key_size_sufficient": cert_info.key_size >= 2048,
                "signature_algorithm_secure": cert_info.signature_algorithm not in ['sha1', 'md5'],
                "days_until_expiry": cert_info.days_until_expiry
            }
            
            # Overall validation
            validation_results["overall_valid"] = (
                validation_results["is_valid"] and
                not validation_results["is_expired"] and
                validation_results["key_size_sufficient"] and
                validation_results["signature_algorithm_secure"] and
                validation_results["days_until_expiry"] > 0
            )
            
            return validation_results
        
        except Exception as e:
            logger.error(f"Failed to validate client certificate: {e}")
            return {
                "error": str(e),
                "overall_valid": False
            }
    
    def create_secure_ssl_context(self) -> ssl.SSLContext:
        """Create secure SSL context for client connections."""
        
        context = ssl.create_default_context()
        
        # Set minimum TLS version
        context.minimum_version = self.min_tls_version
        
        # Set secure cipher suites
        context.set_ciphers(':'.join(self.required_ciphers))
        
        # Disable compression (CRIME attack prevention)
        context.options |= ssl.OP_NO_COMPRESSION
        
        # Enable hostname checking
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        
        return context


# Global encryption validator
encryption_validator = EncryptionValidator()


async def validate_all_connections() -> Dict[str, Any]:
    """Validate encryption for all connections."""
    
    results = {}
    
    # Validate Snowflake connection
    from ..config.manager import config_manager
    snowflake_config = config_manager.get_snowflake_config()
    
    if snowflake_config.account:
        results["snowflake"] = await encryption_validator.validate_snowflake_connection(
            snowflake_config.account
        )
    
    return results
```

