package io.accufolio.app;

import java.net.URI;
import java.util.Locale;

/** A configured server is a single HTTPS origin. No wildcard host matching. */
public final class ServerPolicy {
    private ServerPolicy() {}
    public static String normalize(String input) {
        try {
            URI uri = new URI(input.trim());
            String host = uri.getHost();
            int port = uri.getPort();
            if (!"https".equalsIgnoreCase(uri.getScheme()) || host == null || host.isEmpty()
                    || uri.getUserInfo() != null || uri.getRawQuery() != null || uri.getRawFragment() != null
                    || (uri.getRawPath() != null && !uri.getRawPath().isEmpty() && !"/".equals(uri.getRawPath()))
                    || port == 0 || port > 65535) throw new IllegalArgumentException();
            return new URI("https", null, host.toLowerCase(Locale.ROOT), port == 443 ? -1 : port, "/", null, null).toString();
        } catch (Exception e) {
            throw new IllegalArgumentException("请输入完整 HTTPS 站点地址，例如 https://invest.example.com");
        }
    }
    public static boolean isTrusted(String origin, String target) {
        try {
            URI a = new URI(normalize(origin));
            URI b = new URI(target);
            return "https".equalsIgnoreCase(b.getScheme()) && b.getUserInfo() == null
                    && a.getHost().equalsIgnoreCase(b.getHost())
                    && effectivePort(a) == effectivePort(b);
        } catch (Exception e) { return false; }
    }
    public static boolean canOpenSettings(String origin, String currentPage, String target, boolean mainFrame, boolean gesture) {
        return mainFrame && gesture && "accufolio://settings".equals(target) && isTrusted(origin, currentPage);
    }
    private static int effectivePort(URI uri) { return uri.getPort() == -1 ? 443 : uri.getPort(); }
}
