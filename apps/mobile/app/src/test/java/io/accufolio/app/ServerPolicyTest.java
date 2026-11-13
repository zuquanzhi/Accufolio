package io.accufolio.app;
import org.junit.Test;
import static org.junit.Assert.*;
public class ServerPolicyTest {
    @Test public void normalizesHttpsOrigin() { assertEquals("https://example.com/", ServerPolicy.normalize(" https://EXAMPLE.com:443/ ")); }
    @Test public void acceptsSameOriginPaths() { assertTrue(ServerPolicy.isTrusted("https://example.com/", "https://example.com/api/trades?a=1")); }
    @Test public void blocksOtherOriginsAndSchemes() {
        for (String value : new String[]{"http://example.com/", "https://example.com.evil.test/", "javascript:alert(1)", "file:///etc/passwd", "https://example.com:8443/", "https://u:p@example.com/"})
            assertFalse(value, ServerPolicy.isTrusted("https://example.com/", value));
    }
    @Test public void rejectsInvalidServerConfiguration() {
        for (String value : new String[]{"http://example.com", "example.com", "https://u:p@example.com", "https://example.com/path", "https://example.com?q=1", "https://example.com#x", "https://example.com:0"}) {
            try { ServerPolicy.normalize(value); fail(value); } catch (IllegalArgumentException expected) {}
        }
    }
}
