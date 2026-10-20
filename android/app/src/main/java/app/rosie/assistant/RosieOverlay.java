package app.rosie.assistant;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.PorterDuff;
import android.graphics.PorterDuffColorFilter;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.Layout;
import android.text.StaticLayout;
import android.text.TextPaint;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;

import java.util.Random;

/** The glitchy pixel-octopus popup that shows over any app while Rosie listens, thinks and speaks (the phone version of the desktop overlay). */
public class RosieOverlay {
    private final Context ctx;
    private final WindowManager wm;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private Panel panel;
    volatile String state = "listening", caption = "";
    volatile float level = 0f;
    Runnable onTap;

    RosieOverlay(Context c) { ctx = c; wm = (WindowManager) c.getSystemService(Context.WINDOW_SERVICE); }

    void show(String st, String cap) {
        state = st; caption = cap;
        ui.post(() -> {
            if (panel != null) return;
            panel = new Panel(ctx);
            float d = ctx.getResources().getDisplayMetrics().density;
            WindowManager.LayoutParams lp = new WindowManager.LayoutParams(WindowManager.LayoutParams.MATCH_PARENT, (int) (320 * d), WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS | WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED, android.graphics.PixelFormat.TRANSLUCENT);
            lp.gravity = Gravity.BOTTOM;
            lp.y = (int) (28 * d);
            panel.setOnClickListener(v -> { if (onTap != null) onTap.run(); });
            try { wm.addView(panel, lp); } catch (Exception e) { panel = null; }
        });
    }

    void set(String st, String cap) { state = st; caption = cap == null ? "" : cap; }

    void hide() {
        ui.post(() -> { if (panel != null) { try { wm.removeView(panel); } catch (Exception ignored) { } panel = null; } });
    }

    class Panel extends View {
        final Bitmap octo;
        final Paint plain = new Paint(), ghostA = new Paint(), ghostB = new Paint(), box = new Paint(Paint.ANTI_ALIAS_FLAG), edge = new Paint(Paint.ANTI_ALIAS_FLAG), bar = new Paint();
        final TextPaint label = new TextPaint(Paint.ANTI_ALIAS_FLAG), cap = new TextPaint(Paint.ANTI_ALIAS_FLAG);
        final float d;
        final long born = SystemClock.uptimeMillis();

        Panel(Context c) {
            super(c);
            d = c.getResources().getDisplayMetrics().density;
            Bitmap b = null;
            try { b = BitmapFactory.decodeStream(c.getAssets().open("octo.png")); } catch (Exception ignored) { }
            octo = b;
            plain.setFilterBitmap(false);
            ghostA.setFilterBitmap(false); ghostB.setFilterBitmap(false);
            box.setColor(Color.argb(235, 3, 5, 12));
            edge.setStyle(Paint.Style.STROKE); edge.setStrokeWidth(2 * d);
            label.setTypeface(Typeface.MONOSPACE); label.setFakeBoldText(true); label.setTextSize(22 * d);
            cap.setTypeface(Typeface.MONOSPACE); cap.setTextSize(14 * d); cap.setColor(Color.argb(230, 220, 235, 245));
        }

        int[] pal() {
            switch (state) {
                case "thinking": return new int[]{Color.rgb(157, 77, 255), Color.rgb(0, 229, 255)};
                case "speaking": return new int[]{Color.rgb(191, 77, 255), Color.rgb(26, 230, 204)};
                default: return new int[]{Color.rgb(0, 229, 255), Color.rgb(157, 77, 255)};
            }
        }

        @Override protected void onDraw(Canvas cv) {
            long t = SystemClock.uptimeMillis();
            int W = getWidth(), H = getHeight();
            int[] p = pal();
            int margin = (int) (14 * d);
            RectF card = new RectF(margin, 0, W - margin, H - 4 * d);
            cv.drawRoundRect(card, 22 * d, 22 * d, box);
            edge.setColor(p[0]);
            cv.drawRoundRect(card, 22 * d, 22 * d, edge);
            if (octo != null) {
                Random rnd = new Random(t / 66 + 7);
                float burst = ((t / 1000f * 1.1f) % 1f) < 0.2f ? 1f : 0f;
                float g = Math.min(1f, 0.12f + 0.5f * level + 0.7f * burst);
                float k = (H * 0.56f) / octo.getHeight();
                float ow = octo.getWidth() * k, oh = octo.getHeight() * k;
                float x0 = (W - ow) / 2f, y0 = 14 * d + 4 * d * (float) Math.sin(t / 600.0);
                float split = (3 + 18 * g) * d;
                ghostA.setColorFilter(new PorterDuffColorFilter(p[0], PorterDuff.Mode.SRC_IN)); ghostA.setAlpha(140);
                ghostB.setColorFilter(new PorterDuffColorFilter(p[1], PorterDuff.Mode.SRC_IN)); ghostB.setAlpha(140);
                int[] sizes = {6, 10, 14, 22, 40};
                for (float sy = 0; sy < octo.getHeight(); ) {
                    float sh = g > 0.2f ? sizes[rnd.nextInt(5)] * 0.5f : 30f;
                    float hh = Math.min(sh, octo.getHeight() - sy);
                    float dx = rnd.nextFloat() < 0.15f + 0.5f * g ? (rnd.nextFloat() * 2 - 1) * 26 * g * d : 0f;
                    for (int layer = 0; layer < 3; layer++) {
                        cv.save();
                        cv.clipRect(card.left, y0 + sy * k, card.right, y0 + (sy + hh) * k + 1);
                        cv.translate(x0 + dx + (layer == 0 ? -split : layer == 1 ? split : 0), y0);
                        cv.scale(k, k);
                        cv.drawBitmap(octo, 0, 0, layer == 0 ? ghostA : layer == 1 ? ghostB : plain);
                        cv.restore();
                    }
                    sy += hh;
                }
                bar.setColor(p[0]);
                if (g > 0.3f) for (int i = 0; i < 3 + 8 * g; i++) { bar.setColor(p[rnd.nextInt(2)]); bar.setAlpha(110); float s2 = (5 + rnd.nextInt(10)) * d; cv.drawRect(x0 + rnd.nextFloat() * ow, y0 + rnd.nextFloat() * oh, x0 + rnd.nextFloat() * ow + s2, y0 + rnd.nextFloat() * oh + s2, bar); }
            }
            cap.setTextAlign(Paint.Align.RIGHT);
            cv.drawText("tap to cancel", W - margin - 16 * d, 26 * d, cap);
            cap.setTextAlign(Paint.Align.LEFT);
            label.setColor(p[0]);
            String dots = state.equals("speaking") ? "" : ".".repeat((int) (t / 400 % 4));
            String txt = (state.equals("listening") ? "listening" : state.equals("thinking") ? "thinking" : "Rosie") + dots;
            cv.drawText(txt, margin + 16 * d, H - 62 * d, label);
            String c = caption;
            if (!c.isEmpty()) {
                StaticLayout sl = StaticLayout.Builder.obtain(c, 0, c.length(), cap, (int) (W - 2 * margin - 32 * d)).setMaxLines(2).setEllipsize(android.text.TextUtils.TruncateAt.END).setAlignment(Layout.Alignment.ALIGN_NORMAL).build();
                cv.save(); cv.translate(margin + 16 * d, H - 52 * d); sl.draw(cv); cv.restore();
            }
            postInvalidateDelayed(66);
        }
    }
}
