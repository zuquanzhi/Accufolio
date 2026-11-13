package io.accufolio.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import android.widget.Toast;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.Locale;

/** Checks the project's GitHub release and verifies the package before handing it to Android. */
final class AppUpdater {
    private static final String API = "https://api.github.com/repos/zuquanzhi/Accufolio/releases/latest";
    private static final long MAX_APK = 100L * 1024 * 1024;
    private static final java.util.concurrent.atomic.AtomicBoolean DOWNLOADING = new java.util.concurrent.atomic.AtomicBoolean();
    private AppUpdater() {}
    private static void ui(Activity a, Runnable task) { a.runOnUiThread(() -> { if (!a.isFinishing() && !a.isDestroyed()) task.run(); }); }
    static void check(Activity a) {
        Toast.makeText(a,"正在检查应用更新…",Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            try {
                JSONObject release = new JSONObject(new String(read(API, 1024 * 1024), "UTF-8"));
                String tag = release.getString("tag_name");
                String version = tag.startsWith("v") ? tag.substring(1) : tag;
                if (!version.matches("[0-9]+\\.[0-9]+\\.[0-9]+")) throw new IOException("不支持的版本格式");
                String current = a.getPackageManager().getPackageInfo(a.getPackageName(), 0).versionName;
                if (compareVersions(version,current) <= 0) { ui(a,()->message(a,"应用已是最新版本（"+current+"）")); return; }
                String filename = "Accufolio-" + version + ".apk";
                String prefix = "https://github.com/zuquanzhi/Accufolio/releases/download/" + tag + "/";
                JSONArray assets = release.getJSONArray("assets"); boolean apk = false, sums = false;
                for(int i=0;i<assets.length();i++) {
                    JSONObject item=assets.getJSONObject(i);
                    String name=item.getString("name"), url=item.getString("browser_download_url");
                    if(name.equals(filename) && url.equals(prefix+filename)) apk=true;
                    if(name.equals("SHA256SUMS") && url.equals(prefix+"SHA256SUMS")) sums=true;
                }
                if(!apk || !sums) throw new IOException("发布文件不完整");
                ui(a,()->new AlertDialog.Builder(a).setTitle("发现新版本 " + version)
                    .setMessage("当前版本："+current+"。下载并验证后，将由 Android 请求安装确认。")
                    .setNegativeButton("稍后",null).setPositiveButton("下载更新",(d,w)->download(a,prefix,filename)).show());
            } catch(Exception e) { ui(a,()->message(a,"检查更新失败，请检查网络后重试。")); }
        },"accufolio-update-check").start();
    }
    static int compareVersions(String a,String b) {
        if (!a.matches("[0-9]+\\.[0-9]+\\.[0-9]+") || !b.matches("[0-9]+\\.[0-9]+\\.[0-9]+")) throw new IllegalArgumentException("version");
        String[] x=a.split("\\."), y=b.split("\\.");
        for(int i=0;i<3;i++) { int c=new java.math.BigInteger(x[i]).compareTo(new java.math.BigInteger(y[i]));if(c!=0)return c; }
        return 0;
    }
    private static void download(Activity a,String prefix,String filename) {
        if (!DOWNLOADING.compareAndSet(false,true)) { Toast.makeText(a,"更新正在下载",Toast.LENGTH_SHORT).show(); return; }
        Toast.makeText(a,"正在下载并验证更新…",Toast.LENGTH_LONG).show();
        new Thread(()->{
            File file=new File(a.getCacheDir(),"verified-update.apk");
            try {
                String sums=new String(read(prefix+"SHA256SUMS",1024*1024),"UTF-8");
                String expected=null;
                for(String line:sums.split("\\r?\\n")) { String[] parts=line.trim().split("\\s+",2);if(parts.length==2 && parts[1].replaceFirst("^\\*","").equals(filename) && parts[0].matches("[0-9a-fA-F]{64}"))expected=parts[0]; }
                if(expected==null) throw new IOException("missing checksum");
                byte[] bytes=read(prefix+filename,MAX_APK);
                byte[] digest=MessageDigest.getInstance("SHA-256").digest(bytes);
                StringBuilder actual=new StringBuilder(); for(byte b:digest) actual.append(String.format(Locale.ROOT,"%02x",b & 255));
                if(!actual.toString().equalsIgnoreCase(expected))throw new IOException("checksum mismatch");
                try(FileOutputStream out=new FileOutputStream(file)){out.write(bytes);}
                PackageManager pm=a.getPackageManager();
                PackageInfo archive=pm.getPackageArchiveInfo(file.getAbsolutePath(),PackageManager.GET_SIGNING_CERTIFICATES);
                PackageInfo installed=pm.getPackageInfo(a.getPackageName(),PackageManager.GET_SIGNING_CERTIFICATES);
                if(archive==null || !a.getPackageName().equals(archive.packageName) || archive.getLongVersionCode()<=installed.getLongVersionCode() || archive.signingInfo==null || installed.signingInfo==null)throw new IOException("invalid package");
                Signature[] signer=archive.signingInfo.getApkContentsSigners(), current=installed.signingInfo.getApkContentsSigners();
                if(!Arrays.equals(signer,current))throw new IOException("signer mismatch");
                ui(a,()->install(a,file));
            } catch(Exception e) { file.delete();ui(a,()->message(a,"下载或验证失败，更新未安装。请稍后重试。")); }
            finally { DOWNLOADING.set(false); }
        },"accufolio-update-download").start();
    }
    private static void install(Activity a,File file) {
        if(!a.getPackageManager().canRequestPackageInstalls()) {
            new AlertDialog.Builder(a).setMessage("请允许 Accufolio 安装更新，返回后再次点击“检查应用更新”。")
                .setNegativeButton("取消",null).setPositiveButton("前往设置",(d,w)->a.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:"+a.getPackageName())))).show();return;
        }
        Uri uri=Uri.parse("content://"+a.getPackageName()+".updates/update.apk");
        Intent intent=new Intent(Intent.ACTION_VIEW).setDataAndType(uri,"application/vnd.android.package-archive").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try { a.startActivity(intent); } catch(Exception e) { message(a,"无法打开系统安装界面。"); }
    }
    private static void message(Activity a,String text) { new AlertDialog.Builder(a).setMessage(text).setPositiveButton("确定",null).show(); }
    private static byte[] read(String address,long max) throws Exception {
        URL url=new URL(address);
        for(int redirects=0;redirects<6;redirects++) {
            String host=url.getHost().toLowerCase(Locale.ROOT);
            if(!url.getProtocol().equals("https") || !(host.equals("api.github.com") || host.equals("github.com") || host.equals("release-assets.githubusercontent.com") || host.equals("objects.githubusercontent.com")))throw new IOException("untrusted host");
            HttpURLConnection c=(HttpURLConnection)url.openConnection();c.setConnectTimeout(15000);c.setReadTimeout(30000);c.setInstanceFollowRedirects(false);c.setRequestProperty("User-Agent","Accufolio-Android");
            int status=c.getResponseCode();
            if(status>=300 && status<400){String location=c.getHeaderField("Location");c.disconnect();if(location==null)throw new IOException("redirect");url=new URL(url,location);continue;}
            if(status!=200){c.disconnect();throw new IOException("HTTP "+status);}
            try(InputStream in=c.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
                byte[] buffer=new byte[16384];int n;long count=0;while((n=in.read(buffer))!=-1){count+=n;if(count>max)throw new IOException("too large");out.write(buffer,0,n);}return out.toByteArray();
            }finally{c.disconnect();}
        }throw new IOException("too many redirects");
    }
}
