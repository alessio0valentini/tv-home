package com.ansub.tvhome;

import android.app.Activity;
import android.app.Dialog;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.Window;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

// The greeting card at the top right: "Ciao <name>," and the UN P-3 jobs open in Rome.
//
// A GitHub Action in this repository reads the agencies' job sites every morning and writes a small
// jobs.json. The card only downloads that file (a few KB), at most every few hours, on a background
// thread, and keeps the last good copy so it shows at once and works offline. Nothing runs while the
// home screen isn't open, so it costs next to nothing on a 2 GB TV.
//
// Turned on at install time:  am start -n com.ansub.tvhome/.MainActivity --es greet "Anna"
// (--es feed <url> reads another jobs.json; --es greet none and --es feed none turn the card off).
final class JobsCard {
    static final String PREF_GREET = "greet", PREF_FEED = "feed";
    private static final String PREF_JSON = "jobsjson", PREF_FETCHED = "jobsfetched";
    static final String DEFAULT_FEED = "https://raw.githubusercontent.com/alessio0valentini/tv-home/main/jobs.json";
    private static final long EVERY_MS = 3 * 60 * 60 * 1000L, RETRY_MS = 10 * 60 * 1000L;
    private static final long STALE_MS = 3 * 24 * 60 * 60 * 1000L; // the Action missed three days in a row
    private static final long NEW_MS = 3 * 24 * 60 * 60 * 1000L;   // seen for the first time in the last 3 days
    static final int WIDTH = 960, TOP = 150; // in the 1920x1080 layout, right of the clock
    private static final int SHOWN = 3;
    private static final int ACCENT = 0xFFFFC266;

    static final class Job {
        String agency, title, grade, location, deadline, url, firstSeen;
        Date due;
        boolean isNew;
    }

    private final Activity a;
    private final SharedPreferences prefs;
    private final float scale;
    private final Typeface regular, semibold;
    private final LinearLayout card;
    private final GradientDrawable bg = new GradientDrawable();
    private final List<Job> jobs = new ArrayList<>();
    private String updatedLabel = "";
    private boolean fetching;
    private long lastTry;

    JobsCard(Activity a, SharedPreferences prefs, float scale, Typeface regular, Typeface semibold) {
        this.a = a;
        this.prefs = prefs;
        this.scale = scale;
        this.regular = regular;
        this.semibold = semibold;
        card = new LinearLayout(a);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(px(32), px(18), px(32), px(18));
        bg.setCornerRadius(px(26));
        bg.setColor(0x13FFFFFF);
        bg.setStroke(Math.max(1, px(2)), 0x17FFFFFF);
        card.setBackground(bg);
        card.setFocusable(true);
        card.setOnFocusChangeListener((v, has) -> {
            bg.setColor(has ? 0x30FFFFFF : 0x13FFFFFF);
            v.animate().scaleX(has ? 1.02f : 1f).scaleY(has ? 1.02f : 1f).setDuration(180).start();
        });
        card.setOnClickListener(v -> showList());
        render(prefs.getString(PREF_JSON, null));
    }

    // On when a name or a feed was given at install time.
    static boolean enabled(SharedPreferences prefs) {
        return prefs.getString(PREF_GREET, null) != null || prefs.getString(PREF_FEED, null) != null;
    }

    View view() { return card; }

    // From onResume: show what we have, and fetch a new copy if this one is a few hours old.
    void refresh() {
        long now = System.currentTimeMillis();
        if (fetching || now - prefs.getLong(PREF_FETCHED, 0) < EVERY_MS || now - lastTry < RETRY_MS) return;
        fetching = true;
        lastTry = now;
        final String feed = prefs.getString(PREF_FEED, DEFAULT_FEED);
        new Thread(() -> {
            String body = null;
            try {
                body = download(feed + (feed.contains("?") ? "&" : "?") + "t=" + (now / EVERY_MS));
                new JSONObject(body); // only keep it if it parses
            } catch (Throwable t) {
                body = null;
            }
            final String got = body;
            a.runOnUiThread(() -> {
                fetching = false;
                if (got == null || a.isDestroyed()) return;
                prefs.edit().putString(PREF_JSON, got).putLong(PREF_FETCHED, System.currentTimeMillis()).apply();
                render(got);
            });
        }, "jobs").start();
    }

    private static String download(String url) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(15000);
        c.setRequestProperty("Accept", "application/json");
        try (InputStream in = c.getInputStream()) {
            if (c.getResponseCode() != 200) throw new Exception("HTTP " + c.getResponseCode());
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            for (int n; (n = in.read(buf)) > 0; ) {
                out.write(buf, 0, n);
                if (out.size() > 512 * 1024) throw new Exception("too big");
            }
            return out.toString("UTF-8");
        } finally {
            c.disconnect();
        }
    }

    // Reads the feed into jobs: drops closed ones, new ones first, then the closest deadline.
    private boolean parse(String json) {
        jobs.clear();
        updatedLabel = "";
        if (json == null) return false;
        try {
            JSONObject o = new JSONObject(json);
            long now = System.currentTimeMillis();
            Date updated = iso(o.optString("updated"));
            if (updated != null && now - updated.getTime() > STALE_MS)
                updatedLabel = "lista ferma al " + new SimpleDateFormat("d MMMM", Locale.ITALIAN).format(updated);
            JSONArray arr = o.optJSONArray("jobs");
            Date today = day(now);
            for (int i = 0; arr != null && i < arr.length(); i++) {
                JSONObject j = arr.getJSONObject(i);
                Job job = new Job();
                job.agency = j.optString("agency");
                job.title = j.optString("title");
                job.grade = j.optString("grade", "P-3");
                job.location = j.optString("location", "Roma");
                job.deadline = j.optString("deadline");
                job.url = j.optString("url");
                job.firstSeen = j.optString("first_seen");
                job.due = date(job.deadline);
                if (job.due != null && job.due.before(today)) continue; // closed since the last update
                Date seen = date(job.firstSeen);
                job.isNew = seen != null && now - seen.getTime() < NEW_MS;
                jobs.add(job);
            }
            Collections.sort(jobs, (x, y) -> {
                if (x.isNew != y.isNew) return x.isNew ? -1 : 1;
                if (x.due == null || y.due == null) return x.due == null ? (y.due == null ? 0 : 1) : -1;
                return x.due.compareTo(y.due);
            });
            return true;
        } catch (Exception e) {
            jobs.clear();
            return false;
        }
    }

    private void render(String json) {
        boolean ok = parse(json);
        card.removeAllViews();
        String name = prefs.getString(PREF_GREET, null);
        TextView hello = text(38, Color.WHITE, semibold);
        hello.setText(name == null ? "Ciao," : "Ciao " + name + ",");
        card.addView(hello);

        int fresh = 0;
        for (Job j : jobs) if (j.isNew) fresh++;
        String summary;
        if (json == null) summary = "sto cercando le posizioni P3 aperte a Roma…";
        else if (!ok) summary = "non riesco a leggere la lista delle posizioni adesso.";
        else if (jobs.isEmpty()) summary = "oggi nessuna posizione P3 aperta a Roma.";
        else if (jobs.size() == 1) summary = "c'è 1 posizione P3 aperta a Roma";
        else summary = "ci sono " + jobs.size() + " posizioni P3 aperte a Roma";
        if (fresh == 1) summary += " · 1 nuova";
        else if (fresh > 1) summary += " · " + fresh + " nuove";
        if (!updatedLabel.isEmpty()) summary += " · " + updatedLabel;
        TextView sum = text(24, 0xB8FFFFFF, regular);
        sum.setText(summary);
        oneLine(sum);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(-1, -2);
        sp.bottomMargin = px(jobs.isEmpty() ? 0 : 8);
        card.addView(sum, sp);

        int rows = jobs.size() > SHOWN ? SHOWN - 1 : jobs.size();
        for (int i = 0; i < rows; i++) card.addView(row(jobs.get(i)), new LinearLayout.LayoutParams(-1, px(36)));
        if (jobs.size() > SHOWN) {
            TextView more = text(22, 0x99FFFFFF, regular);
            more.setText("e altre " + (jobs.size() - rows) + " · premi OK per vederle tutte");
            oneLine(more);
            card.addView(more, new LinearLayout.LayoutParams(-1, px(34)));
        }
    }

    // One job on one line: NUOVA, agency, title (cut if long), and the deadline at the right.
    private View row(Job j) {
        LinearLayout r = new LinearLayout(a);
        r.setOrientation(LinearLayout.HORIZONTAL);
        r.setGravity(Gravity.CENTER_VERTICAL);
        if (j.isNew) {
            TextView tag = text(17, 0xFF1A1206, semibold);
            tag.setText("NUOVA");
            tag.setLetterSpacing(0.06f);
            GradientDrawable pill = new GradientDrawable();
            pill.setCornerRadius(px(8));
            pill.setColor(ACCENT);
            tag.setBackground(pill);
            tag.setPadding(px(8), px(1), px(8), px(1));
            LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(-2, -2);
            tp.rightMargin = px(12);
            r.addView(tag, tp);
        }
        TextView agency = text(24, Color.WHITE, semibold);
        agency.setText(j.agency + "  ");
        r.addView(agency, new LinearLayout.LayoutParams(-2, -2));
        TextView title = text(24, 0xE6FFFFFF, regular);
        title.setText(j.title);
        oneLine(title);
        r.addView(title, new LinearLayout.LayoutParams(0, -2, 1f));
        if (j.due != null) {
            TextView due = text(22, 0xB8FFFFFF, regular);
            due.setText("scade " + new SimpleDateFormat("d MMM", Locale.ITALIAN).format(j.due));
            LinearLayout.LayoutParams dp = new LinearLayout.LayoutParams(-2, -2);
            dp.leftMargin = px(20);
            r.addView(due, dp);
        }
        return r;
    }

    // OK on the card: every job in a side panel; OK on one shows all of it.
    private void showList() {
        if (jobs.isEmpty()) return;
        LinearLayout list = new LinearLayout(a);
        list.setOrientation(LinearLayout.VERTICAL);
        for (final Job j : jobs) {
            LinearLayout item = new LinearLayout(a);
            item.setOrientation(LinearLayout.VERTICAL);
            item.setPadding(px(24), px(14), px(24), px(14));
            final GradientDrawable focus = new GradientDrawable();
            focus.setCornerRadius(px(26));
            focus.setColor(Color.TRANSPARENT);
            item.setBackground(focus);
            item.setFocusable(true);
            item.setOnFocusChangeListener((v, has) -> focus.setColor(has ? 0x38FFFFFF : Color.TRANSPARENT));
            TextView t = text(28, Color.WHITE, semibold);
            t.setText((j.isNew ? "● " : "") + j.title);
            if (j.isNew) {
                android.text.SpannableString s = new android.text.SpannableString(t.getText());
                s.setSpan(new android.text.style.ForegroundColorSpan(ACCENT), 0, 1, 0);
                t.setText(s);
            }
            item.addView(t);
            TextView sub = text(22, 0xB8FFFFFF, regular);
            sub.setText(details(j));
            item.addView(sub);
            item.setOnClickListener(v -> showJob(j));
            list.addView(item);
        }
        sidePanel("Posizioni P3 a Roma", list, px(900));
    }

    private void showJob(Job j) {
        LinearLayout box = new LinearLayout(a);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(px(24), 0, px(24), 0);
        TextView t = text(34, Color.WHITE, semibold);
        t.setText(j.title);
        box.addView(t);
        TextView sub = text(26, 0xD9FFFFFF, regular);
        sub.setText(details(j));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = px(16);
        box.addView(sub, lp);
        if (!TextUtils.isEmpty(j.url)) {
            TextView hint = text(22, 0x99FFFFFF, regular);
            hint.setText("Per candidarti, apri dal telefono o dal computer:");
            LinearLayout.LayoutParams hp = new LinearLayout.LayoutParams(-1, -2);
            hp.topMargin = px(36);
            box.addView(hint, hp);
            TextView link = text(24, ACCENT, regular);
            link.setText(j.url);
            box.addView(link);
        }
        box.setFocusable(true);
        sidePanel(j.agency, box, px(900));
    }

    private static String details(Job j) {
        StringBuilder b = new StringBuilder(j.agency);
        if (!TextUtils.isEmpty(j.grade)) b.append(" · ").append(j.grade);
        if (!TextUtils.isEmpty(j.location)) b.append(" · ").append(j.location);
        if (j.due != null) b.append(" · scade il ")
                .append(new SimpleDateFormat("d MMMM yyyy", Locale.ITALIAN).format(j.due));
        return b.toString();
    }

    // A panel from the right, in the same dark glass as the home screen's own panels. Back closes it.
    private void sidePanel(String title, View content, int width) {
        Dialog d = new Dialog(a, android.R.style.Theme_Material_Dialog_NoActionBar);
        LinearLayout box = new LinearLayout(a);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(px(48), px(92), px(48), px(48));
        TextView head = text(40, Color.WHITE, semibold);
        head.setText(title);
        head.setPadding(px(24), 0, px(24), 0);
        oneLine(head);
        box.addView(head);
        ScrollView scroll = new ScrollView(a);
        scroll.setVerticalScrollBarEnabled(false);
        scroll.addView(content);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, 0, 1f);
        lp.topMargin = px(36);
        box.addView(scroll, lp);
        d.setContentView(box);
        Window w = d.getWindow();
        w.setBackgroundDrawable(new ColorDrawable(0xF7161113));
        w.setLayout(width, -1);
        w.setGravity(Gravity.END);
        w.setDimAmount(0.5f);
        d.show();
    }

    private static Date date(String s) {
        if (TextUtils.isEmpty(s)) return null;
        try {
            SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd", Locale.ROOT);
            return f.parse(s.length() > 10 ? s.substring(0, 10) : s);
        } catch (Exception e) {
            return null;
        }
    }

    private static Date iso(String s) {
        if (TextUtils.isEmpty(s)) return null;
        try {
            SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.ROOT);
            f.setTimeZone(TimeZone.getTimeZone("UTC"));
            return f.parse(s.length() > 19 ? s.substring(0, 19) : s);
        } catch (Exception e) {
            return null;
        }
    }

    private static Date day(long ms) {
        try {
            SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd", Locale.ROOT);
            return f.parse(f.format(new Date(ms)));
        } catch (Exception e) {
            return new Date(ms);
        }
    }

    private static void oneLine(TextView t) {
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
    }

    private TextView text(int size, int color, Typeface tf) {
        TextView t = new TextView(a);
        t.setTextSize(TypedValue.COMPLEX_UNIT_PX, px(size));
        t.setTextColor(color);
        if (tf != null) t.setTypeface(tf);
        t.setIncludeFontPadding(false);
        return t;
    }

    private int px(int v) {
        return Math.round(v * scale);
    }
}
