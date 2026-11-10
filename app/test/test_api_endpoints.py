# test_api_endpoints.py
"""
Script pour tester tous les endpoints après les corrections
"""

import requests
import sys
from datetime import datetime

def test_endpoints(base_url="http://127.0.0.1:8000"):
    print(f"Testing API endpoints on {base_url}")
    print("=" * 60)
    
    endpoints = [
        ("GET", "/"),
        ("GET", "/health"),
        ("GET", "/api/health"),
        ("GET", "/api/status"),
        ("GET", "/api/v1/auth/health"),
        ("GET", "/api/v1/auth/tenants/me"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/tenants/register"),
    ]
    
    results = []
    
    for method, endpoint in endpoints:
        try:
            url = f"{base_url}{endpoint}"
            print(f"\nTesting {method} {endpoint}")
            print(f"URL: {url}")
            
            if method == "GET":
                response = requests.get(url, timeout=5)
            else:
                # Pour POST, envoyer un payload vide
                response = requests.post(url, json={}, timeout=5)
            
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                print("✓ SUCCESS")
                results.append((endpoint, True))
            else:
                print(f"✗ FAILED: {response.text[:100]}")
                results.append((endpoint, False))
                
        except requests.exceptions.ConnectionError:
            print(f"✗ CONNECTION ERROR: Cannot reach {url}")
            results.append((endpoint, False))
        except Exception as e:
            print(f"✗ ERROR: {str(e)}")
            results.append((endpoint, False))
    
    print("\n" + "=" * 60)
    print("SUMMARY:")
    print("=" * 60)
    
    successful = sum(1 for _, success in results if success)
    total = len(results)
    
    for endpoint, success in results:
        status = "✓" if success else "✗"
        print(f"{status} {endpoint}")
    
    print(f"\nSuccess rate: {successful}/{total} ({successful/total*100:.1f}%)")
    
    return successful == total

if __name__ == "__main__":
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    success = test_endpoints(base_url)
    sys.exit(0 if success else 1)