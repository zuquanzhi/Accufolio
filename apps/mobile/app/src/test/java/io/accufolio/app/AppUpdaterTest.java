package io.accufolio.app;
import org.junit.Test;
import static org.junit.Assert.*;
public class AppUpdaterTest {
    @Test public void comparesNumericVersions() {
        assertTrue(AppUpdater.compareVersions("1.10.0", "1.9.9") > 0);
        assertTrue(AppUpdater.compareVersions("2.0.0", "10.0.0") < 0);
        assertEquals(0, AppUpdater.compareVersions("1.2.0", "1.2.0"));
    }
    @Test public void rejectsPreReleaseAndMalformedVersions() {
        for (String version : new String[]{"1.2.0-rc1", "../1.2.0", "1.2"}) {
            try { AppUpdater.compareVersions(version,"1.0.0"); fail(version); } catch (IllegalArgumentException expected) {}
        }
    }
}
