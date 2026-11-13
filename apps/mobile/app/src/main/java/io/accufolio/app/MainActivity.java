package io.accufolio.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Context;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.text.InputType;
import android.util.Base64;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.HttpAuthHandler;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebStorage;
import android.webkit.WebView;
import android.webkit.WebChromeClient;
import android.webkit.WebViewClient;
import android.webkit.WebViewDatabase;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;
import java.io.ByteArrayInputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

public class MainActivity extends Activity {
    private LinearLayout root;
    private WebView web;
    private TextView status;
    private String origin;
    private String authorization;
    private boolean pageFailed;
    private final List<HttpAuthHandler> pendingAuth = new ArrayList<>();
    private AlertDialog loginDialog;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        WebViewDatabase.getInstance(this).clearHttpAuthUsernamePassword();
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(11,20,33));
        root.setOnApplyWindowInsetsListener((v,insets)->{
            v.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());
            return insets;
        });
        setContentView(root);
        String saved = getPreferences(MODE_PRIVATE).getString("server", "");
        if (saved.isEmpty()) setup(); else openServer(saved);
    }

    private int dp(int value) { return (int)(value * getResources().getDisplayMetrics().density); }
    private TextView label(String text, int size) {
        TextView view = new TextView(this); view.setText(text);view.setTextColor(Color.WHITE);view.setTextSize(size);
        view.setPadding(dp(16),dp(12),dp(16),dp(12)); return view;
    }
    private void setup() {
        if (web != null) { web.stopLoading();web.destroy();web=null; }
        authorization = null;
        root.removeAllViews();
        root.addView(label("Accufolio",32));
        root.addView(label("连接你的投资账本",22));
        root.addView(label("输入已部署的 Accufolio HTTPS 地址。应用会加载服务器最新功能，无需重复下载安装。",16));
        EditText input = new EditText(this);
        input.setSingleLine(true); input.setTextColor(Color.WHITE);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("https://invest.example.com");input.setHintTextColor(Color.GRAY);
        input.setText(getPreferences(MODE_PRIVATE).getString("server",""));
        input.setPadding(dp(16),dp(16),dp(16),dp(16));
        root.addView(input);
        TextView error = label("",14); root.addView(error);
        Button connect = new Button(this);connect.setText("连接服务器");root.addView(connect);
        connect.setOnClickListener(v->{
            try {
                String server=ServerPolicy.normalize(input.getText().toString());
                getPreferences(MODE_PRIVATE).edit().putString("server",server).apply();
                WebViewDatabase.getInstance(this).clearHttpAuthUsernamePassword();
                CookieManager.getInstance().removeAllCookies(null);
                WebStorage.getInstance().deleteAllData();
                openServer(server);
            } catch (IllegalArgumentException e) { error.setText(e.getMessage()); }
        });
    }

    @SuppressLint("SetJavaScriptEnabled") // Only the explicitly configured HTTPS origin is permitted; no JS bridge.
    private void openServer(String server) {
        try { origin = ServerPolicy.normalize(server); }
        catch (IllegalArgumentException e) { setup();return; }
        root.removeAllViews();
        LinearLayout bar = new LinearLayout(this);
        TextView title = label("Accufolio",18);
        bar.addView(title,new LinearLayout.LayoutParams(0,dp(56),1));
        Button reload=new Button(this);reload.setText("刷新");bar.addView(reload);
        Button settings=new Button(this);settings.setText("服务器");bar.addView(settings);
        root.addView(bar);
        status=label("正在连接…",13);root.addView(status);
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(11,20,33));
        WebSettings options=web.getSettings();
        options.setJavaScriptEnabled(true);options.setDomStorageEnabled(true);
        options.setAllowFileAccess(false);options.setAllowContentAccess(false);
        options.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        options.setCacheMode(WebSettings.LOAD_NO_CACHE);
        options.setSafeBrowsingEnabled(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient(){
            @Override public void onPageStarted(WebView view,String url,android.graphics.Bitmap icon){pageFailed=false;}
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
                return !ServerPolicy.isTrusted(origin,request.getUrl().toString());
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request){
                if (ServerPolicy.isTrusted(origin,request.getUrl().toString())) return null;
                return new WebResourceResponse("text/plain","UTF-8",403,"Forbidden",null,new ByteArrayInputStream(new byte[0]));
            }
            @Override public void onPageFinished(WebView view,String url){ if(!pageFailed)status.setVisibility(View.GONE); }
            @Override public void onReceivedError(WebView view,WebResourceRequest request,WebResourceError error){
                if(request.isForMainFrame()){pageFailed=true;status.setText("连接失败，请检查服务器地址或网络，然后点击刷新。");status.setVisibility(View.VISIBLE);}
            }
            @Override public void onReceivedHttpAuthRequest(WebView view,HttpAuthHandler handler,String host,String realm){
                if (!URI.create(origin).getHost().equalsIgnoreCase(host)) {handler.cancel();return;}
                promptLogin(handler);
            }
        });
        web.setDownloadListener((url,agent,disposition,mime,length)->{
            if(!ServerPolicy.isTrusted(origin,url))return;
            try{
                DownloadManager.Request request=new DownloadManager.Request(Uri.parse(url));
                if(authorization!=null)request.addRequestHeader("Authorization",authorization);
                request.setTitle("Accufolio 交易导出");request.setMimeType("application/json");
                request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS,"accufolio-trades-"+System.currentTimeMillis()+".json");
                request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                ((DownloadManager)getSystemService(Context.DOWNLOAD_SERVICE)).enqueue(request);
                Toast.makeText(this,"已开始导出，文件保存在下载目录",Toast.LENGTH_LONG).show();
            }catch(Exception e){Toast.makeText(this,"无法导出，请在浏览器中重试",Toast.LENGTH_LONG).show();}
        });
        root.addView(web,new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,0,1));
        reload.setOnClickListener(v->new AlertDialog.Builder(this).setMessage("刷新会清空尚未保存的输入。继续？").setNegativeButton("取消",null).setPositiveButton("刷新",(dialog,which)->web.reload()).show());
        settings.setOnClickListener(v->new AlertDialog.Builder(this).setMessage("切换服务器会关闭当前页面。继续？").setNegativeButton("取消",null).setPositiveButton("继续",(dialog,which)->setup()).show());
        web.loadUrl(origin);
    }

    private void promptLogin(HttpAuthHandler handler){
        pendingAuth.add(handler);
        if(loginDialog!=null && loginDialog.isShowing())return;
        LinearLayout form=new LinearLayout(this);form.setOrientation(LinearLayout.VERTICAL);form.setPadding(dp(20),dp(8),dp(20),dp(8));
        EditText user=new EditText(this);user.setSingleLine(true);user.setHint("用户名");user.setText("admin");form.addView(user);
        EditText pass=new EditText(this);pass.setSingleLine(true);pass.setHint("密码");pass.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);form.addView(pass);
        loginDialog=new AlertDialog.Builder(this).setTitle("登录 "+URI.create(origin).getHost()).setView(form)
            .setPositiveButton("登录",(dialog,which)->{
                String username=user.getText().toString(), password=pass.getText().toString();
                authorization="Basic "+Base64.encodeToString((username+":"+password).getBytes(StandardCharsets.UTF_8),Base64.NO_WRAP);
                for(HttpAuthHandler item:pendingAuth)item.proceed(username,password);pendingAuth.clear();
            }).setNegativeButton("取消",(dialog,which)->cancelAuth()).setOnCancelListener(dialog->cancelAuth()).create();
        loginDialog.show();
    }
    private void cancelAuth(){ for(HttpAuthHandler item:pendingAuth)item.cancel();pendingAuth.clear(); }
    @Override protected void onResume(){super.onResume();if(web!=null){web.onResume();web.evaluateJavascript("if(typeof checkVersion==='function'){checkVersion();}",null);}}
    @Override protected void onPause(){if(web!=null)web.onPause();super.onPause();}
    @Override public void onBackPressed(){if(web!=null && web.canGoBack())web.goBack();else super.onBackPressed();}
    @Override protected void onDestroy(){cancelAuth();if(loginDialog!=null)loginDialog.dismiss();if(web!=null)web.destroy();authorization=null;WebViewDatabase.getInstance(this).clearHttpAuthUsernamePassword();super.onDestroy();}
}
