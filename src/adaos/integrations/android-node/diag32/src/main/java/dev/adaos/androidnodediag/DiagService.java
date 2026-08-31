package dev.adaos.androidnodediag;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.Process;
import android.webkit.WebView;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class DiagService extends Service {
    private static final int PORT = 8777;
    private static final int NOTIFICATION_ID = 8777;
    private static final String CHANNEL_ID = "adaos_node_diag";
    private static volatile boolean running = false;
    private static volatile String lastError = "";

    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private ServerSocket server;

    public static boolean isRunning() {
        return running;
    }

    public static String lastError() {
        return lastError;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        startForegroundCompat();
        startServer();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        startServer();
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        closeServer();
        worker.shutdownNow();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private synchronized void startServer() {
        if (server != null && !server.isClosed()) return;
        worker.execute(new Runnable() {
            @Override
            public void run() {
                try {
                    ServerSocket next = new ServerSocket();
                    next.setReuseAddress(true);
                    next.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), PORT));
                    server = next;
                    running = true;
                    lastError = "";
                    while (!next.isClosed()) {
                        Socket socket = next.accept();
                        handle(socket);
                    }
                } catch (Throwable err) {
                    running = false;
                    lastError = err.getClass().getSimpleName() + ": " + String.valueOf(err.getMessage());
                    closeServer();
                }
            }
        });
    }

    private void handle(Socket socket) {
        try {
            BufferedReader reader = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
            String requestLine = reader.readLine();
            String method = "";
            String path = "/";
            if (requestLine != null) {
                String[] parts = requestLine.split(" ");
                if (parts.length >= 1) method = parts[0];
                if (parts.length >= 2) path = parts[1];
            }
            while (true) {
                String line = reader.readLine();
                if (line == null || line.isEmpty()) break;
            }
            if ("OPTIONS".equalsIgnoreCase(method)) {
                respond(socket, 204, "text/plain", "");
            } else if ("/api/node/status".equals(path)) {
                respond(socket, 200, "application/json", statusJson());
            } else {
                respond(socket, 200, "text/plain", "AdaOS Node ABI probe\nGET /api/node/status\n");
            }
        } catch (Throwable err) {
            lastError = err.getClass().getSimpleName() + ": " + String.valueOf(err.getMessage());
        } finally {
            try {
                socket.close();
            } catch (Exception ignored) {
            }
        }
    }

    private void respond(Socket socket, int status, String contentType, String body) throws Exception {
        byte[] payload = body.getBytes(StandardCharsets.UTF_8);
        String head = "HTTP/1.1 " + status + " OK\r\n"
            + "Content-Type: " + contentType + "; charset=utf-8\r\n"
            + "Content-Length: " + payload.length + "\r\n"
            + "Access-Control-Allow-Origin: *\r\n"
            + "Access-Control-Allow-Methods: GET,POST,OPTIONS\r\n"
            + "Access-Control-Allow-Headers: content-type,authorization,x-adaos-owner-token\r\n"
            + "Access-Control-Allow-Private-Network: true\r\n"
            + "Connection: close\r\n\r\n";
        OutputStream out = socket.getOutputStream();
        out.write(head.getBytes(StandardCharsets.UTF_8));
        out.write(payload);
        out.flush();
    }

    private String statusJson() {
        boolean arm64 = Arrays.asList(Build.SUPPORTED_ABIS).contains("arm64-v8a");
        return "{"
            + "\"ok\":true,"
            + "\"ready\":true,"
            + "\"node_state\":\"ready\","
            + "\"node_id\":\"android-node-abi-probe\","
            + "\"subnet_id\":\"android-node-abi-probe\","
            + "\"environment\":{\"local_auth_required\":false},"
            + "\"runtime\":{"
            + "\"app_version\":\"" + json(BuildConfig.VERSION_NAME) + "\","
            + "\"diagnostic_only\":true,"
            + "\"full_node_runtime\":false,"
            + "\"full_node_arm64_supported\":" + arm64 + ","
            + "\"python_available\":false,"
            + "\"y_py_available\":false,"
            + "\"yjs_ready\":false,"
            + "\"android_api\":" + Build.VERSION.SDK_INT + ","
            + "\"android_release\":\"" + json(Build.VERSION.RELEASE) + "\","
            + "\"manufacturer\":\"" + json(Build.MANUFACTURER) + "\","
            + "\"model\":\"" + json(Build.MODEL) + "\","
            + "\"supported_abis\":\"" + json(Arrays.toString(Build.SUPPORTED_ABIS)) + "\","
            + "\"cpu_abi\":\"" + json(Build.CPU_ABI) + "\","
            + "\"process_64_bit\":" + Process.is64Bit() + ","
            + "\"webview_package\":\"" + json(webViewPackage()) + "\""
            + "}"
            + "}";
    }

    private String webViewPackage() {
        if (Build.VERSION.SDK_INT < 26) return "unknown";
        try {
            return String.valueOf(WebView.getCurrentWebViewPackage());
        } catch (Throwable err) {
            return err.getClass().getSimpleName();
        }
    }

    private String json(String value) {
        return String.valueOf(value).replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private void startForegroundCompat() {
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26 && manager != null) {
            NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "AdaOS Node Probe", NotificationManager.IMPORTANCE_LOW);
            manager.createNotificationChannel(channel);
        }
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
            ? new Notification.Builder(this, CHANNEL_ID)
            : new Notification.Builder(this);
        Notification notification = builder
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle("AdaOS Node ABI Probe")
            .setContentText("Loopback status on 127.0.0.1:8777")
            .setOngoing(true)
            .build();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private synchronized void closeServer() {
        try {
            if (server != null) server.close();
        } catch (Exception ignored) {
        }
        server = null;
    }
}
