import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

results = []

def test(name, func):
    try:
        func()
        results.append(f"[OK] {name}")
    except Exception as e:
        results.append(f"[FAIL] {name}: {e}")

def test_lru_cache_basic_operations():
    from service.agent_service.cache.lru_cache import LRUCache
    cache = LRUCache(max_size=3, ttl_seconds=3600)
    cache.set("key1", {"data": "value1"})
    cache.set("key2", {"data": "value2"})
    cache.set("key3", {"data": "value3"})
    assert cache.get("key1") == {"data": "value1"}
    assert cache.get("key2") == {"data": "value2"}
    assert cache.get("key3") == {"data": "value3"}
    assert cache.get("nonexistent") is None
    stats = cache.get_stats()
    assert stats["size"] == 3
    assert stats["hits"] == 3
    assert stats["misses"] == 1

def test_lru_cache_eviction():
    from service.agent_service.cache.lru_cache import LRUCache
    cache = LRUCache(max_size=2, ttl_seconds=3600)
    cache.set("key1", {"data": "value1"})
    cache.set("key2", {"data": "value2"})
    cache.set("key3", {"data": "value3"})
    assert cache.get("key1") is None
    assert cache.get("key2") == {"data": "value2"}
    assert cache.get("key3") == {"data": "value3"}

def test_lru_cache_ttl_expiration():
    import time
    from service.agent_service.cache.lru_cache import LRUCache
    cache = LRUCache(max_size=10, ttl_seconds=1)
    cache.set("key1", {"data": "value1"})
    assert cache.get("key1") == {"data": "value1"}
    time.sleep(1.5)
    assert cache.get("key1") is None

def test_cache_key_generator_consistency():
    from service.agent_service.cache.cache_key_generator import CacheKeyGenerator
    message = {"role": "user", "content": "test content"}
    target_ratio = 0.5
    key1 = CacheKeyGenerator.generate(message, target_ratio)
    key2 = CacheKeyGenerator.generate(message, target_ratio)
    assert key1 == key2
    assert len(key1) == 64

def test_cache_key_generator_different_content():
    from service.agent_service.cache.cache_key_generator import CacheKeyGenerator
    message1 = {"role": "user", "content": "content1"}
    message2 = {"role": "user", "content": "content2"}
    key1 = CacheKeyGenerator.generate(message1, 0.5)
    key2 = CacheKeyGenerator.generate(message2, 0.5)
    assert key1 != key2

def test_cache_key_generator_different_ratio():
    from service.agent_service.cache.cache_key_generator import CacheKeyGenerator
    message = {"role": "user", "content": "test content"}
    key1 = CacheKeyGenerator.generate(message, 0.4)
    key2 = CacheKeyGenerator.generate(message, 0.5)
    assert key1 != key2

def test_cache_key_generator_normalize_content():
    from service.agent_service.cache.cache_key_generator import CacheKeyGenerator
    content1 = "test  content"
    content2 = "test content"
    normalized1 = CacheKeyGenerator.normalize_content(content1)
    normalized2 = CacheKeyGenerator.normalize_content(content2)
    assert normalized1 == normalized2

def test_sqlite_cache_backend_basic_operations():
    import os
    import tempfile
    import shutil
    from service.agent_service.cache.sqlite_cache import SQLiteCacheBackend
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_cache.db")
    try:
        cache = SQLiteCacheBackend(db_path=db_path)
        cache.set(
            key="test_key",
            original_hash="hash123",
            value={"summary": "test summary"},
            target_ratio=0.5,
            original_tokens=1000,
            compressed_tokens=500,
            ttl_seconds=3600
        )
        result = cache.get("test_key")
        assert result is not None
        assert result["summary"] == "test summary"
        stats = cache.get_stats()
        assert stats["total_entries"] == 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_sqlite_cache_backend_expiration():
    import os
    import time
    import tempfile
    import shutil
    from service.agent_service.cache.sqlite_cache import SQLiteCacheBackend
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_cache.db")
    try:
        cache = SQLiteCacheBackend(db_path=db_path)
        cache.set(
            key="test_key",
            original_hash="hash123",
            value={"summary": "test summary"},
            target_ratio=0.5,
            original_tokens=1000,
            compressed_tokens=500,
            ttl_seconds=1
        )
        assert cache.get("test_key") is not None
        time.sleep(1.5)
        assert cache.get("test_key") is None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_token_calculator_estimate_tokens():
    from service.agent_service.service.compression_service import TokenCalculator
    class MockSettings:
        def get(self, key):
            if key == "llm:model":
                return "gpt-4o-mini"
            raise KeyError(key)
    calculator = TokenCalculator(MockSettings())
    text = "This is a test text"
    tokens = calculator.estimate_tokens(text)
    assert tokens > 0

def test_token_calculator_context_window():
    from service.agent_service.service.compression_service import TokenCalculator
    class MockSettings:
        def get(self, key):
            if key == "llm:model":
                return "gpt-4o-mini"
            raise KeyError(key)
    calculator = TokenCalculator(MockSettings())
    assert calculator.context_window == 128000

def test_token_calculator_usage_rate():
    from service.agent_service.service.compression_service import TokenCalculator
    class MockSettings:
        def get(self, key):
            if key == "llm:model":
                return "gpt-4o-mini"
            raise KeyError(key)
    calculator = TokenCalculator(MockSettings())
    messages = [
        {"role": "user", "content": "test content" * 100},
        {"role": "assistant", "content": "response content" * 100},
    ]
    usage_rate = calculator.calculate_usage_rate(messages)
    assert 0 < usage_rate < 1

def test_convolution_window_build():
    from service.agent_service.service.compression_service import ConvolutionCompressor
    class MockSettings:
        def __init__(self):
            self._data = {
                "compression": {
                    "enabled": True,
                    "compression_version": "v1",
                    "trigger_threshold": 0.8,
                    "target_min": 0.4,
                    "target_max": 0.5,
                    "keep_recent": 3,
                    "min_length_to_compress": 200,
                    "cache_enabled": True,
                    "cache_ttl_seconds": 3600,
                    "l1_cache_size": 100,
                },
                "llm": {"model": "gpt-4o-mini"}
            }
        def get(self, key):
            parts = key.split(":")
            node = self._data
            for part in parts:
                if not isinstance(node, dict) or part not in node:
                    raise KeyError(key)
                node = node[part]
            return node
    class MockLLM:
        def chat(self, messages, temperature=0.7):
            import json
            return json.dumps({"role": "assistant", "summary": "test summary"})
    from service.agent_service.cache import CompressionCache
    settings = MockSettings()
    llm = MockLLM()
    cache = CompressionCache(settings)
    compressor = ConvolutionCompressor(settings, llm, cache)
    messages = [
        {"role": "user", "content": "question1"},
        {"role": "assistant", "content": "answer1"},
        {"role": "user", "content": "question2"},
    ]
    window = compressor._build_window(messages, 1)
    assert window.prev == {"role": "user", "content": "question1"}
    assert window.target == {"role": "assistant", "content": "answer1"}
    assert window.next == {"role": "user", "content": "question2"}
    assert window.target_index == 1

def test_convolution_window_boundary_first():
    from service.agent_service.service.compression_service import ConvolutionCompressor
    class MockSettings:
        def __init__(self):
            self._data = {
                "compression": {
                    "enabled": True,
                    "compression_version": "v1",
                    "trigger_threshold": 0.8,
                    "target_min": 0.4,
                    "target_max": 0.5,
                    "keep_recent": 3,
                    "min_length_to_compress": 200,
                    "cache_enabled": True,
                    "cache_ttl_seconds": 3600,
                    "l1_cache_size": 100,
                },
                "llm": {"model": "gpt-4o-mini"}
            }
        def get(self, key):
            parts = key.split(":")
            node = self._data
            for part in parts:
                if not isinstance(node, dict) or part not in node:
                    raise KeyError(key)
                node = node[part]
            return node
    class MockLLM:
        def chat(self, messages, temperature=0.7):
            import json
            return json.dumps({"role": "assistant", "summary": "test summary"})
    from service.agent_service.cache import CompressionCache
    settings = MockSettings()
    llm = MockLLM()
    cache = CompressionCache(settings)
    compressor = ConvolutionCompressor(settings, llm, cache)
    messages = [
        {"role": "user", "content": "question1"},
        {"role": "assistant", "content": "answer1"},
    ]
    window = compressor._build_window(messages, 0)
    assert window.prev is None
    assert window.target == {"role": "user", "content": "question1"}
    assert window.next == {"role": "assistant", "content": "answer1"}

def test_convolution_window_boundary_last():
    from service.agent_service.service.compression_service import ConvolutionCompressor
    class MockSettings:
        def __init__(self):
            self._data = {
                "compression": {
                    "enabled": True,
                    "compression_version": "v1",
                    "trigger_threshold": 0.8,
                    "target_min": 0.4,
                    "target_max": 0.5,
                    "keep_recent": 3,
                    "min_length_to_compress": 200,
                    "cache_enabled": True,
                    "cache_ttl_seconds": 3600,
                    "l1_cache_size": 100,
                },
                "llm": {"model": "gpt-4o-mini"}
            }
        def get(self, key):
            parts = key.split(":")
            node = self._data
            for part in parts:
                if not isinstance(node, dict) or part not in node:
                    raise KeyError(key)
                node = node[part]
            return node
    class MockLLM:
        def chat(self, messages, temperature=0.7):
            import json
            return json.dumps({"role": "assistant", "summary": "test summary"})
    from service.agent_service.cache import CompressionCache
    settings = MockSettings()
    llm = MockLLM()
    cache = CompressionCache(settings)
    compressor = ConvolutionCompressor(settings, llm, cache)
    messages = [
        {"role": "user", "content": "question1"},
        {"role": "assistant", "content": "answer1"},
    ]
    window = compressor._build_window(messages, 1)
    assert window.prev == {"role": "user", "content": "question1"}
    assert window.target == {"role": "assistant", "content": "answer1"}
    assert window.next is None

def test_compression_service_compress_messages():
    from service.agent_service.service.compression_service import CompressionService
    class MockSettings:
        def __init__(self):
            self._data = {
                "compression": {
                    "enabled": True,
                    "compression_version": "v1",
                    "trigger_threshold": 0.8,
                    "target_min": 0.4,
                    "target_max": 0.5,
                    "keep_recent": 3,
                    "min_length_to_compress": 200,
                    "cache_enabled": True,
                    "cache_ttl_seconds": 3600,
                    "l1_cache_size": 100,
                },
                "llm": {"model": "gpt-4o-mini"}
            }
        def get(self, key):
            parts = key.split(":")
            node = self._data
            for part in parts:
                if not isinstance(node, dict) or part not in node:
                    raise KeyError(key)
                node = node[part]
            return node
    class MockLLM:
        def chat(self, messages, temperature=0.7):
            import json
            return json.dumps({"role": "assistant", "summary": "test summary"})
    settings = MockSettings()
    llm = MockLLM()
    service = CompressionService(settings, llm)
    messages = [
        {"role": "user", "content": "short message"},
        {"role": "assistant", "content": "This is a long response content, " * 100},
        {"role": "user", "content": "question2"},
    ]
    result = service.compress_messages(messages)
    assert len(result) == 3

def test_compression_service_disabled():
    from service.agent_service.service.compression_service import CompressionService
    class MockSettings:
        def __init__(self):
            self._data = {
                "compression": {
                    "enabled": False,
                    "compression_version": "v1",
                    "trigger_threshold": 0.8,
                    "target_min": 0.4,
                    "target_max": 0.5,
                    "keep_recent": 3,
                    "min_length_to_compress": 200,
                    "cache_enabled": True,
                    "cache_ttl_seconds": 3600,
                    "l1_cache_size": 100,
                },
                "llm": {"model": "gpt-4o-mini"}
            }
        def get(self, key):
            parts = key.split(":")
            node = self._data
            for part in parts:
                if not isinstance(node, dict) or part not in node:
                    raise KeyError(key)
                node = node[part]
            return node
    class MockLLM:
        def chat(self, messages, temperature=0.7):
            import json
            return json.dumps({"role": "assistant", "summary": "test summary"})
    settings = MockSettings()
    llm = MockLLM()
    service = CompressionService(settings, llm)
    messages = [{"role": "user", "content": "test"}]
    result = service.compress_messages(messages)
    assert result == messages

def test_compression_service_stats():
    from service.agent_service.service.compression_service import CompressionService
    class MockSettings:
        def __init__(self):
            self._data = {
                "compression": {
                    "enabled": True,
                    "compression_version": "v1",
                    "trigger_threshold": 0.8,
                    "target_min": 0.4,
                    "target_max": 0.5,
                    "keep_recent": 3,
                    "min_length_to_compress": 200,
                    "cache_enabled": True,
                    "cache_ttl_seconds": 3600,
                    "l1_cache_size": 100,
                },
                "llm": {"model": "gpt-4o-mini"}
            }
        def get(self, key):
            parts = key.split(":")
            node = self._data
            for part in parts:
                if not isinstance(node, dict) or part not in node:
                    raise KeyError(key)
                node = node[part]
            return node
    class MockLLM:
        def chat(self, messages, temperature=0.7):
            import json
            return json.dumps({"role": "assistant", "summary": "test summary"})
    settings = MockSettings()
    llm = MockLLM()
    service = CompressionService(settings, llm)
    stats = service.get_stats()
    assert "total_requests" in stats
    assert "compressed_requests" in stats
    assert "avg_compression_time" in stats

def test_compression_cache_integration():
    import uuid
    from service.agent_service.cache import CompressionCache
    class MockSettings:
        def __init__(self):
            self._data = {
                "compression": {
                    "cache_enabled": True,
                    "cache_ttl_seconds": 3600,
                    "l1_cache_size": 100,
                }
            }
        def get(self, key):
            parts = key.split(":")
            node = self._data
            for part in parts:
                if not isinstance(node, dict) or part not in node:
                    raise KeyError(key)
                node = node[part]
            return node
    settings = MockSettings()
    cache = CompressionCache(settings)
    unique_content = f"unique test content {uuid.uuid4()}"
    message = {"role": "user", "content": unique_content}
    target_ratio = 0.5
    result = {
        "role": "user",
        "summary": "test summary",
        "key_points": ["point1"],
    }
    assert cache.get(message, target_ratio) is None
    cache.set(message, target_ratio, result, 100, 50)
    cached = cache.get(message, target_ratio)
    assert cached == result
    hit_rate = cache.get_hit_rate()
    assert hit_rate["total_requests"] == 2
    assert hit_rate["l1_hits"] >= 1

def test_extract_content_from_dict():
    from service.agent_service.service.compression_service import ConvolutionCompressor
    class MockSettings:
        def __init__(self):
            self._data = {
                "compression": {
                    "enabled": True,
                    "compression_version": "v1",
                    "trigger_threshold": 0.8,
                    "target_min": 0.4,
                    "target_max": 0.5,
                    "keep_recent": 3,
                    "min_length_to_compress": 200,
                    "cache_enabled": True,
                    "cache_ttl_seconds": 3600,
                    "l1_cache_size": 100,
                },
                "llm": {"model": "gpt-4o-mini"}
            }
        def get(self, key):
            parts = key.split(":")
            node = self._data
            for part in parts:
                if not isinstance(node, dict) or part not in node:
                    raise KeyError(key)
                node = node[part]
            return node
    class MockLLM:
        def chat(self, messages, temperature=0.7):
            import json
            return json.dumps({"role": "assistant", "summary": "test summary"})
    from service.agent_service.cache import CompressionCache
    settings = MockSettings()
    llm = MockLLM()
    cache = CompressionCache(settings)
    compressor = ConvolutionCompressor(settings, llm, cache)
    msg1 = {"role": "user", "content": "text content"}
    assert compressor._extract_content(msg1) == "text content"
    msg2 = {
        "role": "user",
        "parts": [
            {"type": "text", "text": "part1"},
            {"type": "text", "text": "part2"},
        ]
    }
    assert compressor._extract_content(msg2) == "part1 part2"

def test_build_compressed_message():
    from service.agent_service.service.compression_service import ConvolutionCompressor
    class MockSettings:
        def __init__(self):
            self._data = {
                "compression": {
                    "enabled": True,
                    "compression_version": "v1",
                    "trigger_threshold": 0.8,
                    "target_min": 0.4,
                    "target_max": 0.5,
                    "keep_recent": 3,
                    "min_length_to_compress": 200,
                    "cache_enabled": True,
                    "cache_ttl_seconds": 3600,
                    "l1_cache_size": 100,
                },
                "llm": {"model": "gpt-4o-mini"}
            }
        def get(self, key):
            parts = key.split(":")
            node = self._data
            for part in parts:
                if not isinstance(node, dict) or part not in node:
                    raise KeyError(key)
                node = node[part]
            return node
    class MockLLM:
        def chat(self, messages, temperature=0.7):
            import json
            return json.dumps({"role": "assistant", "summary": "test summary"})
    from service.agent_service.cache import CompressionCache
    settings = MockSettings()
    llm = MockLLM()
    cache = CompressionCache(settings)
    compressor = ConvolutionCompressor(settings, llm, cache)
    original = {"role": "assistant", "content": "original content"}
    compressed_result = {
        "role": "assistant",
        "summary": "summary",
        "key_points": ["point1"],
    }
    result = compressor._build_compressed_message(original, compressed_result, 0)
    assert result["role"] == "assistant"
    assert result["compressed"] == True
    assert "original_length" in result
    assert "compressed_length" in result
    assert "[Compressed record #0]" in result["content"] or "[压缩记录 #0]" in result["content"]

test("LRUCache basic operations", test_lru_cache_basic_operations)
test("LRUCache eviction", test_lru_cache_eviction)
test("LRUCache TTL expiration", test_lru_cache_ttl_expiration)
test("CacheKeyGenerator consistency", test_cache_key_generator_consistency)
test("CacheKeyGenerator different content", test_cache_key_generator_different_content)
test("CacheKeyGenerator different ratio", test_cache_key_generator_different_ratio)
test("CacheKeyGenerator normalize content", test_cache_key_generator_normalize_content)
test("SQLiteCacheBackend basic operations", test_sqlite_cache_backend_basic_operations)
test("SQLiteCacheBackend expiration", test_sqlite_cache_backend_expiration)
test("TokenCalculator estimate tokens", test_token_calculator_estimate_tokens)
test("TokenCalculator context window", test_token_calculator_context_window)
test("TokenCalculator usage rate", test_token_calculator_usage_rate)
test("ConvolutionCompressor window build", test_convolution_window_build)
test("ConvolutionCompressor window boundary first", test_convolution_window_boundary_first)
test("ConvolutionCompressor window boundary last", test_convolution_window_boundary_last)
test("CompressionService compress messages", test_compression_service_compress_messages)
test("CompressionService disabled", test_compression_service_disabled)
test("CompressionService stats", test_compression_service_stats)
test("CompressionCache integration", test_compression_cache_integration)
test("ConvolutionCompressor extract content", test_extract_content_from_dict)
test("ConvolutionCompressor build compressed message", test_build_compressed_message)

with open("test_results.txt", "w", encoding="utf-8") as f:
    for r in results:
        f.write(r + "\n")
    f.write("\nTotal: " + str(len(results)) + " tests\n")
    passed = sum(1 for r in results if r.startswith("[OK]"))
    f.write("Passed: " + str(passed) + "\n")
    f.write("Failed: " + str(len(results) - passed) + "\n")

print("Test results written to test_results.txt")
