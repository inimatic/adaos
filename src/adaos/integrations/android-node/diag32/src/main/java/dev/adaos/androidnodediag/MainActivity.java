package dev.adaos.androidnodediag;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.Arrays;

public final class MainActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private TextView statusView;
    private TextView factsView;
    private final Runnable refresh = new Runnable() {
        @Override
        public void run() {
            render();
            main.postDelayed(this, 1000L);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildContent());
        requestNotificationPermission();
        startDiagService();
    }

    @Override
    protected void onStart() {
        super.onStart();
        refresh.run();
    }

    @Override
    protected void onStop() {
        main.removeCallbacks(refresh);
        super.onStop();
    }

    private View buildContent() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.rgb(17, 24, 39));

        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setGravity(Gravity.CENTER_HORIZONTAL);
        int pad = dp(22);
        content.setPadding(pad, dp(44), pad, dp(28));

        TextView title = label("AdaOS Node ABI Probe", 28f, Color.WHITE, true);
        TextView subtitle = label("Diagnostic APK for 32-bit/legacy Android install testing", 15f, Color.rgb(156, 163, 175), false);
        statusView = label("Starting loopback...", 20f, Color.rgb(129, 140, 248), true);
        factsView = label("", 14f, Color.rgb(229, 231, 235), false);

        content.addView(title);
        content.addView(subtitle);
        content.addView(statusView);
        content.addView(factsView);
        content.addView(button("Start loopback", new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                startDiagService();
            }
        }));
        content.addView(button("Stop loopback", new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                stopService(new Intent(MainActivity.this, DiagService.class));
            }
        }));
        content.addView(button("Open Inimatic LO", new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://inimatic.com/?zone=lo&try_local_hub=1&runtime_debug=1")));
            }
        }));

        scroll.addView(content, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return scroll;
    }

    private void startDiagService() {
        Intent intent = new Intent(this, DiagService.class);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
    }

    private void render() {
        String error = DiagService.lastError();
        boolean running = DiagService.isRunning();
        statusView.setText(running ? "Loopback ready: http://127.0.0.1:8777/api/node/status" : "Loopback stopped");
        statusView.setTextColor(running ? Color.rgb(52, 211, 153) : Color.rgb(248, 113, 113));
        factsView.setText(deviceFacts(error));
    }

    private String deviceFacts(String error) {
        StringBuilder out = new StringBuilder();
        out.append("APK: ").append(BuildConfig.VERSION_NAME).append('\n');
        out.append("Package: ").append(BuildConfig.APPLICATION_ID).append('\n');
        out.append("Android API: ").append(Build.VERSION.SDK_INT).append(" / ").append(Build.VERSION.RELEASE).append('\n');
        out.append("Device: ").append(Build.MANUFACTURER).append(' ').append(Build.MODEL).append('\n');
        out.append("Supported ABIs: ").append(Arrays.toString(Build.SUPPORTED_ABIS)).append('\n');
        out.append("Legacy CPU_ABI: ").append(Build.CPU_ABI).append('\n');
        out.append("Process is 64-bit: ").append(Process.is64Bit()).append('\n');
        out.append("Full arm64 node supported: ").append(Arrays.asList(Build.SUPPORTED_ABIS).contains("arm64-v8a")).append('\n');
        out.append("WebView package: ").append(webViewPackage()).append('\n');
        out.append("Full node runtime: not bundled in this probe").append('\n');
        if (error != null && !error.trim().isEmpty()) {
            out.append("Loopback error: ").append(error).append('\n');
        }
        return out.toString();
    }

    private String webViewPackage() {
        if (Build.VERSION.SDK_INT < 26) return "unknown";
        try {
            Object pkg = WebView.getCurrentWebViewPackage();
            return String.valueOf(pkg);
        } catch (Throwable err) {
            return err.getClass().getSimpleName();
        }
    }

    private TextView label(String text, float sp, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(sp);
        view.setTextColor(color);
        view.setGravity(Gravity.CENTER);
        view.setPadding(0, dp(6), 0, dp(6));
        if (bold) view.setTypeface(view.getTypeface(), 1);
        return view;
    }

    private Button button(String text, View.OnClickListener listener) {
        Button button = new Button(this);
        button.setText(text);
        button.setOnClickListener(listener);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, dp(8), 0, 0);
        button.setLayoutParams(params);
        return button;
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] { Manifest.permission.POST_NOTIFICATIONS }, 100);
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
