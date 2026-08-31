package dev.adaos.androidnodepydiag;

import android.app.Activity;
import android.graphics.Color;
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

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.Arrays;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private TextView statusView;
    private TextView factsView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildContent());
        render("Ready to start Python", "");
        runPythonProbe();
    }

    @Override
    protected void onDestroy() {
        worker.shutdownNow();
        super.onDestroy();
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

        content.addView(label("AdaOS Python ABI Probe", 28f, Color.WHITE, true));
        content.addView(label("Chaquopy CPython 3.11 on armeabi-v7a", 15f, Color.rgb(156, 163, 175), false));
        statusView = label("", 20f, Color.rgb(129, 140, 248), true);
        factsView = label("", 14f, Color.rgb(229, 231, 235), false);
        content.addView(statusView);
        content.addView(factsView);
        content.addView(button("Run Python Probe", new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                runPythonProbe();
            }
        }));

        scroll.addView(content, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return scroll;
    }

    private void runPythonProbe() {
        render("Starting Python...", "Java facts:\n" + javaFacts());
        worker.execute(new Runnable() {
            @Override
            public void run() {
                try {
                    if (!Python.isStarted()) {
                        Python.start(new AndroidPlatform(MainActivity.this));
                    }
                    Python py = Python.getInstance();
                    PyObject result = py.getModule("diag_probe").callAttr("probe", javaFacts());
                    postResult("Python started", result.toString());
                } catch (Throwable err) {
                    postResult("Python failed: " + err.getClass().getSimpleName(), stackTrace(err));
                }
            }
        });
    }

    private void postResult(final String status, final String facts) {
        main.post(new Runnable() {
            @Override
            public void run() {
                render(status, facts);
            }
        });
    }

    private void render(String status, String facts) {
        statusView.setText(status);
        statusView.setTextColor(status.contains("failed") ? Color.rgb(248, 113, 113) : Color.rgb(52, 211, 153));
        factsView.setText(facts);
    }

    private String javaFacts() {
        return "APK: " + BuildConfig.VERSION_NAME + "\n"
            + "Package: " + BuildConfig.APPLICATION_ID + "\n"
            + "Android API: " + Build.VERSION.SDK_INT + " / " + Build.VERSION.RELEASE + "\n"
            + "Device: " + Build.MANUFACTURER + " " + Build.MODEL + "\n"
            + "Supported ABIs: " + Arrays.toString(Build.SUPPORTED_ABIS) + "\n"
            + "Legacy CPU_ABI: " + Build.CPU_ABI + "\n"
            + "Process is 64-bit: " + Process.is64Bit() + "\n"
            + "WebView package: " + webViewPackage();
    }

    private String webViewPackage() {
        if (Build.VERSION.SDK_INT < 26) return "unknown";
        try {
            return String.valueOf(WebView.getCurrentWebViewPackage());
        } catch (Throwable err) {
            return err.getClass().getSimpleName();
        }
    }

    private String stackTrace(Throwable err) {
        StringWriter sw = new StringWriter();
        err.printStackTrace(new PrintWriter(sw));
        return javaFacts() + "\n\n" + sw.toString();
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

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
