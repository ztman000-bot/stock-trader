package com.ztman.stocktraderremote;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.webkit.SafeBrowsingResponse;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;

public class MainActivity extends Activity {
    private static final String PREFS = "stock_trader_remote";
    private static final String KEY_URL = "server_url";
    private WebView webView;
    private TextView status;
    private String serverUrl = "";
    private String allowedHost = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.rgb(5, 8, 13));
        getWindow().setNavigationBarColor(Color.rgb(5, 8, 13));
        buildUi();
        configureWebView();

        serverUrl = getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_URL, "");
        if (serverUrl == null || serverUrl.isBlank()) {
            showServerDialog(true);
        } else {
            loadServer(serverUrl);
        }
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(5, 8, 13));

        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(12), dp(8), dp(8), dp(8));
        bar.setBackgroundColor(Color.rgb(10, 17, 26));

        TextView title = new TextView(this);
        title.setText("Stock Day Trader");
        title.setTextColor(Color.WHITE);
        title.setTextSize(18);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        bar.addView(title, new LinearLayout.LayoutParams(0, dp(48), 1f));

        Button address = new Button(this);
        address.setText("주소");
        address.setOnClickListener(v -> showServerDialog(false));
        bar.addView(address, new LinearLayout.LayoutParams(dp(72), dp(48)));

        Button reload = new Button(this);
        reload.setText("새로고침");
        reload.setOnClickListener(v -> {
            if (webView != null && !serverUrl.isBlank()) webView.reload();
        });
        bar.addView(reload, new LinearLayout.LayoutParams(dp(92), dp(48)));

        status = new TextView(this);
        status.setText("Tailscale 연결 후 서버 주소를 설정하세요.");
        status.setTextColor(Color.rgb(180, 195, 208));
        status.setTextSize(12);
        status.setPadding(dp(12), dp(5), dp(12), dp(5));
        status.setBackgroundColor(Color.rgb(8, 13, 20));

        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(5, 8, 13));

        root.addView(bar, new LinearLayout.LayoutParams(-1, dp(64)));
        root.addView(status, new LinearLayout.LayoutParams(-1, dp(28)));
        root.addView(webView, new LinearLayout.LayoutParams(-1, 0, 1f));
        setContentView(root);
    }

    private void configureWebView() {
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setDatabaseEnabled(false);
        s.setGeolocationEnabled(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        WebView.setWebContentsDebuggingEnabled(false);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (isAllowed(uri)) return false;
                Toast.makeText(MainActivity.this, "설정한 Stock Trader 서버 외 이동을 차단했습니다.", Toast.LENGTH_SHORT).show();
                return true;
            }

            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase();
                if ((scheme.equals("http") || scheme.equals("https")) && !isAllowed(uri)) {
                    return new WebResourceResponse(
                            "text/plain", "utf-8",
                            new ByteArrayInputStream("blocked".getBytes(StandardCharsets.UTF_8)));
                }
                return super.shouldInterceptRequest(view, request);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                status.setText("연결됨 · " + displayOrigin(url));
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    status.setText("연결 실패 · Tailscale/공기계 서버 상태 확인");
                }
            }

            @Override
            public void onSafeBrowsingHit(WebView view, WebResourceRequest request, int threatType, SafeBrowsingResponse callback) {
                callback.backToSafety(true);
            }
        });
    }

    private boolean isAllowed(Uri uri) {
        if (uri == null || allowedHost.isBlank()) return false;
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase();
        String host = uri.getHost() == null ? "" : uri.getHost();
        return (scheme.equals("http") || scheme.equals("https")) && host.equalsIgnoreCase(allowedHost);
    }

    private String normalize(String raw) {
        String x = raw == null ? "" : raw.trim();
        if (x.isBlank()) return "";
        if (!x.startsWith("http://") && !x.startsWith("https://")) x = "http://" + x;
        Uri uri = Uri.parse(x);
        if (uri.getHost() == null || uri.getHost().isBlank()) return "";
        String path = uri.getPath();
        if (path == null || path.isBlank() || path.equals("/")) {
            Uri.Builder b = uri.buildUpon().path("/classic");
            x = b.build().toString();
        }
        return x;
    }

    private void loadServer(String raw) {
        String normalized = normalize(raw);
        if (normalized.isBlank()) {
            Toast.makeText(this, "유효한 서버 주소를 입력하세요.", Toast.LENGTH_LONG).show();
            showServerDialog(true);
            return;
        }
        Uri uri = Uri.parse(normalized);
        allowedHost = uri.getHost() == null ? "" : uri.getHost();
        serverUrl = normalized;
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_URL, serverUrl).apply();
        status.setText("연결 중 · " + displayOrigin(serverUrl));
        webView.loadUrl(serverUrl);
    }

    private void showServerDialog(boolean required) {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("http://100.x.x.x:8000/classic");
        input.setText(serverUrl);
        input.setSelectAllOnFocus(true);
        int pad = dp(20);
        LinearLayout wrap = new LinearLayout(this);
        wrap.setPadding(pad, 0, pad, 0);
        wrap.addView(input, new LinearLayout.LayoutParams(-1, -2));

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Stock Trader 서버 주소")
                .setMessage("메인폰의 Tailscale을 켠 뒤 공기계의 Tailscale 주소와 8000 포트를 입력하세요. 주소는 이 휴대폰에만 저장됩니다.")
                .setView(wrap)
                .setPositiveButton("연결", null)
                .setNegativeButton(required ? null : "취소", null)
                .create();
        dialog.setCanceledOnTouchOutside(!required);
        dialog.setOnShowListener(v -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v2 -> {
            String normalized = normalize(input.getText().toString());
            if (normalized.isBlank()) {
                input.setError("예: http://100.x.x.x:8000/classic");
                return;
            }
            dialog.dismiss();
            loadServer(normalized);
        }));
        dialog.show();
    }

    private String displayOrigin(String url) {
        try {
            Uri u = Uri.parse(url);
            String port = u.getPort() > 0 ? ":" + u.getPort() : "";
            return u.getScheme() + "://" + u.getHost() + port;
        } catch (Exception e) {
            return "Stock Trader";
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.destroy();
        }
        super.onDestroy();
    }
}
