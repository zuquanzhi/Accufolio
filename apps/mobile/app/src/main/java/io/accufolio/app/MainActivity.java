package io.accufolio.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.Dialog;
import android.app.DownloadManager;
import android.content.Context;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Environment;
import android.text.InputType;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.view.inputmethod.InputMethodManager;
import android.webkit.CookieManager;
import android.webkit.HttpAuthHandler;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebChromeClient;
import android.webkit.WebViewClient;
import android.webkit.WebViewDatabase;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import java.io.ByteArrayInputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

public class MainActivity extends Activity {
    private int INK = Color.rgb(28,34,48), MUTED = Color.rgb(103,113,131);
    private int BACKGROUND = Color.rgb(245,247,251), BLUE = Color.rgb(40,100,229), SURFACE=Color.WHITE, SHEET=Color.rgb(252,253,255);
    private LinearLayout root, recovery;
    private WebView web;
    private TextView status;
    private Button recoverySettings;
    private String origin, authorization, mainDocument;
    private boolean pageFailed;
    private final List<HttpAuthHandler> pendingAuth = new ArrayList<>();
    private AlertDialog loginDialog;
    private Dialog settingsDialog;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        boolean dark=(getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)==Configuration.UI_MODE_NIGHT_YES;
        if(dark){BACKGROUND=Color.rgb(17,20,26);INK=Color.rgb(243,245,249);MUTED=Color.rgb(168,179,196);SURFACE=Color.rgb(29,34,43);SHEET=Color.rgb(32,37,48);}
        WebViewDatabase.getInstance(this).clearHttpAuthUsernamePassword();
        root = column(0);
        root.setBackgroundColor(BACKGROUND);
        root.setOnApplyWindowInsetsListener((v,insets)->{
            v.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());
            return insets;
        });
        getWindow().setStatusBarColor(BACKGROUND);
        getWindow().setNavigationBarColor(BACKGROUND);
        getWindow().getDecorView().setSystemUiVisibility(dark?0:View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        setContentView(root);
        String saved = getPreferences(MODE_PRIVATE).getString("server", "");
        if (saved.isEmpty()) setup(); else openServer(saved);
    }

    private int dp(int value) { return (int)(value * getResources().getDisplayMetrics().density); }
    private LinearLayout column(int padding) {
        LinearLayout view=new LinearLayout(this);view.setOrientation(LinearLayout.VERTICAL);
        view.setPadding(dp(padding),dp(padding),dp(padding),dp(padding));return view;
    }
    private GradientDrawable surface(int color,int radius) {
        GradientDrawable shape=new GradientDrawable();shape.setColor(color);shape.setCornerRadius(dp(radius));return shape;
    }
    private TextView label(String text, int size, int color) {
        TextView view=new TextView(this);view.setText(text);view.setTextSize(size);view.setTextColor(color);
        view.setPadding(0,dp(6),0,dp(6));return view;
    }
    private Button button(String text, boolean primary) {
        Button button=new Button(this);button.setText(text);button.setAllCaps(false);button.setTextSize(16);
        button.setTextColor(primary?Color.WHITE:INK);button.setMinHeight(dp(52));button.setMinimumHeight(dp(52));
        button.setPadding(dp(18),dp(10),dp(18),dp(10));button.setBackground(surface(primary?BLUE:SURFACE,17));
        button.setElevation(primary?dp(2):0);return button;
    }
    private void gap(LinearLayout parent,int height){View spacer=new View(this);parent.addView(spacer,new LinearLayout.LayoutParams(1,dp(height)));}
    private EditText addressField() {
        EditText input=new EditText(this);input.setSingleLine(true);input.setTextColor(INK);input.setTextSize(16);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("https://invest.example.com");input.setHintTextColor(MUTED);
        input.setText(getPreferences(MODE_PRIVATE).getString("server",""));
        input.setContentDescription("连接地址");input.setBackground(surface(SURFACE,16));
        input.setPadding(dp(16),dp(18),dp(16),dp(18));return input;
    }
    private void setup() {
        root.removeAllViews();
        ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);
        LinearLayout welcome=column(24);scroll.addView(welcome);gap(welcome,12);
        LinearLayout brand=new LinearLayout(this);brand.setGravity(Gravity.CENTER_VERTICAL);
        ImageView icon=new ImageView(this);icon.setImageResource(R.drawable.app_icon);
        brand.addView(icon,new LinearLayout.LayoutParams(dp(48),dp(48)));
        TextView name=label("Accufolio",26,INK);name.setTypeface(null,Typeface.BOLD);name.setPadding(dp(14),0,0,0);brand.addView(name);welcome.addView(brand);gap(welcome,28);
        TextView heading=label("连接你的投资账户",24,INK);heading.setTypeface(null,Typeface.BOLD);welcome.addView(heading);
        welcome.addView(label("输入你的专属站点地址，让每一次积累都清晰可见。",15,MUTED));gap(welcome,18);
        welcome.addView(label("连接地址",13,MUTED));EditText input=addressField();welcome.addView(input);
        TextView error=label("",14,Color.rgb(175,52,60));error.setVisibility(View.GONE);error.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);welcome.addView(error);
        gap(welcome,12);welcome.addView(label("使用 HTTPS 安全连接。账户数据由你自己的站点保存。",13,MUTED));
        root.addView(scroll,new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,0,1));
        LinearLayout footer=column(20);Button connect=button("连接",true);footer.addView(connect);gap(footer,10);
        connect.setOnClickListener(v->{
            try { String server=ServerPolicy.normalize(input.getText().toString());
                ((InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(input.getWindowToken(),0);
                saveServer(server);openServer(server);
            } catch(IllegalArgumentException e){error.setText(e.getMessage());error.setVisibility(View.VISIBLE);}
        });
        Button settings=button("应用设置",false);settings.setOnClickListener(v->showSettings());footer.addView(settings);root.addView(footer);
    }
    private void saveServer(String server){
        getPreferences(MODE_PRIVATE).edit().putString("server",server).apply();
        authorization=null;WebViewDatabase.getInstance(this).clearHttpAuthUsernamePassword();
        CookieManager.getInstance().removeAllCookies(null);
        // Device-local journals remain in WebStorage and are isolated by HTTPS origin.
    }
    private void showSettings(){
        if(settingsDialog!=null && settingsDialog.isShowing())return;
        Dialog dialog=new Dialog(this);dialog.requestWindowFeature(Window.FEATURE_NO_TITLE);settingsDialog=dialog;
        LinearLayout content=column(24);content.setBackground(surface(SHEET,28));
        TextView title=label("应用设置",23,INK);title.setTypeface(null,Typeface.BOLD);content.addView(title);
        String version="1.3.0";try{version=getPackageManager().getPackageInfo(getPackageName(),0).versionName;}catch(Exception ignored){}
        content.addView(label("Accufolio "+version,13,MUTED));gap(content,18);
        if(web!=null){
            Button address=button("连接地址",false);content.addView(address);
            TextView host=label(URI.create(origin).getHost(),13,MUTED);host.setSingleLine(true);content.addView(host);gap(content,8);
            address.setOnClickListener(v->{dialog.dismiss();editAddress();});
            Button reload=button("重新载入",false);content.addView(reload);gap(content,8);
            reload.setOnClickListener(v->{dialog.dismiss();new AlertDialog.Builder(this).setTitle("重新载入页面？").setMessage("未保存的输入会被清空，已保存的记录不受影响。")
                .setNegativeButton("取消",null).setPositiveButton("重新载入",(d,w)->{if(web!=null)web.reload();}).show();});
        }
        Button update=button("检查更新",false);content.addView(update);gap(content,18);update.setOnClickListener(v->{dialog.dismiss();AppUpdater.check(this);});
        Button close=button("完成",true);content.addView(close);close.setOnClickListener(v->dialog.dismiss());
        ScrollView sheetScroll=new ScrollView(this);sheetScroll.setBackground(surface(SHEET,28));sheetScroll.setClipToOutline(true);sheetScroll.addView(content);dialog.setContentView(sheetScroll);dialog.setCanceledOnTouchOutside(true);
        Window window=dialog.getWindow();if(window!=null){window.setBackgroundDrawableResource(android.R.color.transparent);window.addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND);WindowManager.LayoutParams attributes=window.getAttributes();attributes.gravity=Gravity.BOTTOM;attributes.dimAmount=.25f;attributes.y=dp(12);window.setAttributes(attributes);}
        dialog.show();if(window!=null){
            int width=getResources().getDisplayMetrics().widthPixels-dp(24);
            content.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(0,View.MeasureSpec.UNSPECIFIED));
            window.setLayout(width,Math.min(content.getMeasuredHeight(),Math.max(dp(160),root.getHeight()-dp(40))));
        }
    }
    private void editAddress(){
        LinearLayout form=column(20);form.addView(label("更换连接会关闭当前页面。随手记仍保留在原站点的设备存储中。",14,MUTED));
        EditText input=addressField();form.addView(input);TextView error=label("",14,Color.rgb(175,52,60));form.addView(error);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("连接地址").setView(form).setNegativeButton("取消",null).setPositiveButton("连接",null).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{
            try{String next=ServerPolicy.normalize(input.getText().toString());new AlertDialog.Builder(this).setTitle("切换连接？").setMessage("当前页面未保存的输入会被清空。")
                .setNegativeButton("取消",null).setPositiveButton("继续",(confirm,which)->{dialog.dismiss();saveServer(next);openServer(next);}).show();
            }catch(IllegalArgumentException e){error.setText(e.getMessage());}
        }));dialog.show();
    }
    private void connectionFailed(String message){connectionFailed(message,true);}
    private void connectionFailed(String message,boolean hideDocument){
        pageFailed=true;
        // Preserve the WebView, history and device storage, but never expose Chromium's error page.
        if(hideDocument && web!=null)web.setVisibility(View.INVISIBLE);
        status.setVisibility(View.VISIBLE);status.setText(message);recoverySettings.setText("连接设置");recoverySettings.setVisibility(View.VISIBLE);recovery.setVisibility(View.VISIBLE);
    }

    @SuppressLint("SetJavaScriptEnabled") // Only the explicitly configured HTTPS origin is permitted; no JS bridge.
    private void openServer(String server) {
        try { origin=ServerPolicy.normalize(server); } catch(IllegalArgumentException e){setup();return;}
        cancelAuth();if(loginDialog!=null)loginDialog.dismiss();
        if(web!=null){web.stopLoading();web.destroy();web=null;}
        root.removeAllViews();
        web=new WebView(this);web.setBackgroundColor(BACKGROUND);
        WebSettings options=web.getSettings();options.setJavaScriptEnabled(true);options.setDomStorageEnabled(true);
        options.setUserAgentString(options.getUserAgentString()+" AccufolioAndroid/1.3.0");
        options.setAllowFileAccess(false);options.setAllowContentAccess(false);options.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        options.setCacheMode(WebSettings.LOAD_NO_CACHE);options.setSafeBrowsingEnabled(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);web.setWebChromeClient(new WebChromeClient());
        recovery=column(18);recovery.setBackground(surface(SURFACE,20));status=label("正在连接…",14,MUTED);recovery.addView(status);
        recoverySettings=button("连接设置",false);recoverySettings.setOnClickListener(v->showSettings());recovery.addView(recoverySettings);
        web.setWebViewClient(new WebViewClient(){
            @Override public void onPageStarted(WebView view,String url,android.graphics.Bitmap icon){if(!ServerPolicy.isTrusted(origin,url))return;mainDocument=url;view.setVisibility(View.VISIBLE);pageFailed=false;status.setVisibility(View.VISIBLE);status.setText("正在连接…");recoverySettings.setText("连接设置");recoverySettings.setVisibility(View.VISIBLE);recovery.setVisibility(View.VISIBLE);}
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
                String target=request.getUrl().toString();
                if(ServerPolicy.canOpenSettings(origin,view.getUrl(),target,request.isForMainFrame(),request.hasGesture())){showSettings();return true;}
                return !ServerPolicy.isTrusted(origin,target);
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request){
                if(ServerPolicy.isTrusted(origin,request.getUrl().toString()))return null;
                return new WebResourceResponse("text/plain","UTF-8",403,"Forbidden",null,new ByteArrayInputStream(new byte[0]));
            }
            @Override public void onPageFinished(WebView view,String url){
                if(!pageFailed && ServerPolicy.isTrusted(origin,url)){
                    // Older self-hosted pages may lack the new settings link. Keep recovery reachable.
                    view.evaluateJavascript("!!document.querySelector('a[href=\"accufolio://settings\"]')",result->{
                        if(pageFailed || web!=view)return;
                        if("true".equals(result))recovery.setVisibility(View.GONE);
                        else{status.setVisibility(View.GONE);recoverySettings.setText("应用设置");recovery.setVisibility(View.VISIBLE);}
                    });
                }
            }
            @Override public void onReceivedError(WebView view,WebResourceRequest request,WebResourceError error){if(request.isForMainFrame())connectionFailed("暂时无法连接。你可以检查网络，或更换连接地址。");}
            @Override public void onReceivedHttpError(WebView view,WebResourceRequest request,WebResourceResponse response){if(request.isForMainFrame())connectionFailed("站点暂时不可用（"+response.getStatusCode()+"）。请稍后重试或检查连接。");}
            @Override public void onReceivedSslError(WebView view,SslErrorHandler handler,SslError error){
                handler.cancel();
                // SSL errors can belong to a subresource. Do not blank a healthy main document.
                boolean mainFrame=error.getUrl()!=null && error.getUrl().equals(mainDocument);
                connectionFailed("无法验证站点安全证书。请检查连接地址和证书设置。",mainFrame);
            }
            @Override public void onReceivedHttpAuthRequest(WebView view,HttpAuthHandler handler,String host,String realm){
                if(!URI.create(origin).getHost().equalsIgnoreCase(host)){handler.cancel();return;}promptLogin(handler);
            }
        });
        web.setDownloadListener((url,agent,disposition,mime,length)->{
            if(!ServerPolicy.isTrusted(origin,url))return;
            try{DownloadManager.Request request=new DownloadManager.Request(Uri.parse(url));if(authorization!=null)request.addRequestHeader("Authorization",authorization);
                request.setTitle("Accufolio 交易导出");request.setMimeType("application/json");request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS,"accufolio-trades-"+System.currentTimeMillis()+".json");
                request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);((DownloadManager)getSystemService(Context.DOWNLOAD_SERVICE)).enqueue(request);
                Toast.makeText(this,"已开始导出，文件保存在下载目录",Toast.LENGTH_LONG).show();
            }catch(Exception e){Toast.makeText(this,"无法导出，请在浏览器中重试",Toast.LENGTH_LONG).show();}
        });
        root.addView(web,new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,0,1));root.addView(recovery);mainDocument=origin;web.loadUrl(origin);
    }

    private void promptLogin(HttpAuthHandler handler){
        pendingAuth.add(handler);if(loginDialog!=null && loginDialog.isShowing())return;
        LinearLayout form=column(20);EditText user=new EditText(this);user.setSingleLine(true);user.setHint("用户名");user.setText("admin");form.addView(user);
        EditText pass=new EditText(this);pass.setSingleLine(true);pass.setHint("密码");pass.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);form.addView(pass);
        loginDialog=new AlertDialog.Builder(this).setTitle("登录 "+URI.create(origin).getHost()).setView(form)
            .setPositiveButton("登录",(dialog,which)->{String username=user.getText().toString(),password=pass.getText().toString();authorization="Basic "+Base64.encodeToString((username+":"+password).getBytes(StandardCharsets.UTF_8),Base64.NO_WRAP);
                for(HttpAuthHandler item:pendingAuth)item.proceed(username,password);pendingAuth.clear();})
            .setNegativeButton("取消",(dialog,which)->{cancelAuth();connectionFailed("尚未登录。你可以重新载入，或更换连接地址。");})
            .setOnCancelListener(dialog->{cancelAuth();connectionFailed("尚未登录。你可以重新载入，或更换连接地址。");}).create();loginDialog.show();
    }
    private void cancelAuth(){for(HttpAuthHandler item:pendingAuth)item.cancel();pendingAuth.clear();}
    @Override protected void onResume(){super.onResume();if(web!=null){web.onResume();web.evaluateJavascript("if(typeof checkVersion==='function'){checkVersion();}",null);}}
    @Override public void onConfigurationChanged(Configuration config){
        super.onConfigurationChanged(config);
        if(settingsDialog!=null && settingsDialog.isShowing()){settingsDialog.dismiss();root.post(this::showSettings);}
    }
    @Override protected void onPause(){if(web!=null)web.onPause();super.onPause();}
    @Override public void onBackPressed(){
        if(web!=null && ServerPolicy.isTrusted(origin,web.getUrl())){
            web.evaluateJavascript("typeof window.accufolioBack==='function' ? window.accufolioBack() : false",result->{if(!"true".equals(result))navigateBack();});
        }else navigateBack();
    }
    private void navigateBack(){if(web!=null && web.canGoBack())web.goBack();else super.onBackPressed();}
    @Override protected void onDestroy(){cancelAuth();if(loginDialog!=null)loginDialog.dismiss();if(settingsDialog!=null)settingsDialog.dismiss();if(web!=null)web.destroy();authorization=null;WebViewDatabase.getInstance(this).clearHttpAuthUsernamePassword();super.onDestroy();}
}
