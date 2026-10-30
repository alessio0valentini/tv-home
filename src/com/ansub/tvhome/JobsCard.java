package com.ansub.tvhome;

import android.app.Activity;
import android.app.Dialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
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
import java.text.Normalizer;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.Collections;
import java.util.Date;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;
import java.util.regex.Pattern;

// The greeting card at the top right: "Ciao <name>," and the UN P-3 jobs open in Rome.
//
// A GitHub Action in this repository reads the agencies' job sites twice a day and writes a small
// jobs.json. The card only downloads that file (a few KB), at most every few hours, on a background
// thread, and keeps the last good copy so it shows at once and works offline. Nothing runs while the
// home screen isn't open, so it costs next to nothing on a 2 GB TV.
//
// Set at install time (install.sh passes these):
//   --es greet "Anna"                          the name in the greeting
//   --es keywords "strategy,innovation,..."    show only jobs with one of these words in the title
//                                              ("-some phrase" leaves a phrase out); none = every job
//   --es feed <url>                            another jobs.json
// The name and the words stay on the TV; the public jobs.json has every job.
final class JobsCard {
    static final String PREF_GREET = "greet", PREF_FEED = "feed", PREF_KEYWORDS = "keywords";
    static final String PREF_JSON = "jobsjson", PREF_FETCHED = "jobsfetched";
    static final String DEFAULT_FEED = "https://raw.githubusercontent.com/alessio0valentini/tv-home/main/jobs.json";
    private static final long EVERY_MS = 3 * 60 * 60 * 1000L, RETRY_MS = 10 * 60 * 1000L;
    private static final long DAY_MS = 24 * 60 * 60 * 1000L;
    private static final long STALE_MS = 3 * DAY_MS; // the Action missed three days in a row
    private static final long NEW_MS = 3 * DAY_MS;   // first seen in the last three days
    static final int WIDTH = 960, TOP = 150; // in the 1920x1080 layout, right of the clock
    private static final int SHOWN = 3;
    private static final int GOLD = 0xFFFFC266, RED = 0xFFFF7A7A, ORANGE = 0xFFFFB061, DIM = 0xB8FFFFFF;
    private static final Locale IT = Locale.ITALIAN;

    // A word in the keyword list also finds its forms and its French, Spanish and Italian titles.
    private static final Map<String, String[]> STEMS = new LinkedHashMap<>();
    static {
        STEMS.put("innovation", new String[] {"innova"});
        STEMS.put("transformation", new String[] {"transforma", "trasforma"});
        STEMS.put("change", new String[] {"change", "cambio", "cambiament"});
        STEMS.put("management", new String[] {"management", "gestion"});
        STEMS.put("strategy", new String[] {"strateg"});
    }
    // Climate change is another line of work: it never counts as "change".
    private static final String[] NOT_CHANGE = {"climate change", "changement climatique", "changements climatiques",
            "cambio climatico", "cambiamento climatico", "cambiamenti climatici"};

    // Agency badges: muted colours that read well behind white text on the dark wallpapers.
    private static final Map<String, Integer> AGENCY_COLORS = new LinkedHashMap<>();
    static {
        AGENCY_COLORS.put("FAO", 0xFF2E7D8C);
        AGENCY_COLORS.put("WFP", 0xFF2D63C8);
        AGENCY_COLORS.put("IFAD", 0xFF3C8D4F);
        AGENCY_COLORS.put("UNDP", 0xFF4B5BC4);
        AGENCY_COLORS.put("UNHCR", 0xFF1F7FB4);
        AGENCY_COLORS.put("UNICEF", 0xFF138FA3);
        AGENCY_COLORS.put("UNIDO", 0xFFA8457A);
        AGENCY_COLORS.put("IOM", 0xFF7050B0);
        AGENCY_COLORS.put("ILO", 0xFFB45A26);
        AGENCY_COLORS.put("UNOPS", 0xFF8C6D12);
    }
    private static final int OTHER_AGENCY = 0xFF55607A;
    private static final List<String> SMALL_WORDS = Arrays.asList("of", "and", "the", "for", "on", "in", "at", "to",
            "de", "du", "des", "la", "le", "les", "et", "y", "del", "el", "per", "di", "e");

    static final class Job {
        String agency, badge, title, grade, location, deadline, url;
        Date due;
        boolean isNew;
        List<String> words = new ArrayList<>();
        List<String> qr;
    }

    private final Activity a;
    private final SharedPreferences prefs;
    private final float scale;
    private final Typeface regular, semibold;
    private final LinearLayout card;
    private final GradientDrawable bg = new GradientDrawable();
    private final List<Job> jobs = new ArrayList<>();
    private boolean filtered; // keywords are set, so jobs holds only the matching ones
    private Date updated;
    private int sitesOk;
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
        card.setPadding(px(32), px(18), px(32), px(20));
        card.setContentDescription("Offerte di lavoro");
        bg.setCornerRadius(px(26));
        bg.setColor(0x13FFFFFF);
        bg.setStroke(Math.max(1, px(2)), 0x17FFFFFF);
        card.setBackground(bg);
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
        render(prefs.getString(PREF_JSON, null)); // a day may have passed: deadlines and "new" move on
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
                if (out.size() > 1024 * 1024) throw new Exception("too big");
            }
            return out.toString("UTF-8");
        } finally {
            c.disconnect();
        }
    }

    // ---- reading the feed ----

    // Reads the feed into jobs: drops closed ones and, with keywords set, the ones without them.
    // New ones first, then the closest deadline.
    private boolean parse(String json) {
        jobs.clear();
        updated = null;
        sitesOk = 0;
        String kw = prefs.getString(PREF_KEYWORDS, null);
        filtered = kw != null && !kw.trim().isEmpty();
        if (json == null) return false;
        try {
            JSONObject o = new JSONObject(json);
            long now = System.currentTimeMillis();
            updated = iso(o.optString("updated"));
            JSONObject sources = o.optJSONObject("sources");
            if (sources != null) for (Iterator<String> it = sources.keys(); it.hasNext(); ) {
                JSONObject s = sources.optJSONObject(it.next());
                if (s != null && s.optBoolean("ok")) sitesOk++;
            }
            List<String[]> wanted = new ArrayList<>();
            List<String> skip = new ArrayList<>(Arrays.asList(NOT_CHANGE));
            if (filtered) for (String w : kw.split(",")) {
                w = norm(w).trim();
                if (w.startsWith("-") && w.length() > 1) skip.add(w.substring(1).trim());
                else if (!w.isEmpty()) wanted.add(new String[] {w});
            }
            JSONArray arr = o.optJSONArray("jobs");
            Date today = day(now);
            for (int i = 0; arr != null && i < arr.length(); i++) {
                JSONObject j = arr.getJSONObject(i);
                Job job = new Job();
                job.agency = j.optString("agency");
                job.badge = badge(job.agency);
                job.title = j.optString("title");
                job.grade = j.optString("grade", "P-3");
                job.location = j.optString("location", "Roma");
                job.deadline = j.optString("deadline");
                job.url = j.optString("url");
                job.due = date(job.deadline);
                if (job.due != null && job.due.before(today)) continue; // closed since the last update
                Date seen = date(j.optString("first_seen"));
                job.isNew = seen != null && now - seen.getTime() < NEW_MS;
                if (filtered) {
                    job.words = matches(job.title, wanted, skip);
                    if (job.words.isEmpty()) continue;
                }
                JSONArray qr = j.optJSONArray("qr");
                if (qr != null) {
                    job.qr = new ArrayList<>();
                    for (int r = 0; r < qr.length(); r++) job.qr.add(qr.optString(r));
                }
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

    // The keywords found in a title (as the user wrote them), after taking out the left-out phrases.
    static List<String> matches(String title, List<String[]> wanted, List<String> skip) {
        String t = " " + norm(title) + " ";
        for (String s : skip) t = t.replace(s, " ");
        List<String> out = new ArrayList<>();
        for (String[] w : wanted) {
            String[] stems = STEMS.containsKey(w[0]) ? STEMS.get(w[0]) : new String[] {w[0]};
            for (String stem : stems) {
                if (Pattern.compile("\\b" + Pattern.quote(stem)).matcher(t).find()) {
                    out.add(w[0]);
                    break;
                }
            }
        }
        return out;
    }

    // Lower case, no accents: "Stratégie" and "strategie" are the same word.
    private static String norm(String s) {
        return Normalizer.normalize(s == null ? "" : s, Normalizer.Form.NFD)
                .replaceAll("\\p{M}+", "").toLowerCase(Locale.ROOT).replaceAll("\\s+", " ");
    }

    // A short name for the badge: "FAO" and "UN-Habitat" stay, "UN Secretariat" is "UN",
    // "Department of Economic and Social Affairs" is "DESA".
    static String badge(String agency) {
        String a = agency == null ? "" : agency.trim();
        if (a.length() <= 10) return a;
        for (String w : a.split("[\\s/]+")) if (w.matches("[A-Z]{2,8}")) return w; // a name with its acronym in it
        StringBuilder b = new StringBuilder();
        for (String w : a.split("[\\s\\-/]+"))
            if (!w.isEmpty() && !SMALL_WORDS.contains(w.toLowerCase(Locale.ROOT))) b.append(Character.toUpperCase(w.charAt(0)));
        return b.length() >= 2 ? b.toString() : a.substring(0, 8);
    }

    // ---- the card ----

    private void render(String json) {
        boolean ok = parse(json);
        card.removeAllViews();
        String name = prefs.getString(PREF_GREET, null);
        TextView hello = text(38, Color.WHITE, semibold);
        hello.setText(name == null ? "Ciao," : "Ciao " + name + ",");
        oneLine(hello);
        card.addView(hello);

        String forYou = filtered ? " per te" : "";
        int fresh = 0;
        for (Job j : jobs) if (j.isNew) fresh++;
        String summary;
        if (json == null) summary = "sto cercando le posizioni P3 aperte a Roma…";
        else if (!ok) summary = "non riesco a leggere la lista delle posizioni adesso.";
        else if (jobs.isEmpty()) summary = "oggi nessuna posizione P3 a Roma" + forYou + ".";
        else if (jobs.size() == 1) summary = "c'è 1 posizione P3 a Roma" + forYou;
        else summary = "ci sono " + jobs.size() + " posizioni P3 a Roma" + forYou;
        if (fresh == 1) summary += " · 1 nuova";
        else if (fresh > 1) summary += " · " + fresh + " nuove";
        if (updated != null && System.currentTimeMillis() - updated.getTime() > STALE_MS)
            summary += " · lista ferma al " + new SimpleDateFormat("d MMMM", IT).format(updated);
        TextView sum = text(24, DIM, regular);
        sum.setText(summary);
        oneLine(sum);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(-1, -2);
        sp.topMargin = px(4);
        card.addView(sum, sp);

        if (ok && jobs.isEmpty()) {
            // Nothing to show: say the search is still running, and when it last looked.
            TextView note = text(21, 0x99FFFFFF, regular);
            note.setText("Controllo " + (sitesOk > 0 ? sitesOk + " siti ONU" : "i siti ONU")
                    + " due volte al giorno: appena ne esce una, la vedi qui.");
            note.setMaxLines(2);
            LinearLayout.LayoutParams np = new LinearLayout.LayoutParams(-1, -2);
            np.topMargin = px(14);
            card.addView(note, np);
            if (updated != null) {
                TextView when = text(19, 0x73FFFFFF, regular);
                when.setText("Aggiornato " + when(updated));
                LinearLayout.LayoutParams wp = new LinearLayout.LayoutParams(-1, -2);
                wp.topMargin = px(6);
                card.addView(when, wp);
            }
        }

        int rows = jobs.size() > SHOWN ? SHOWN - 1 : jobs.size();
        for (int i = 0; i < rows; i++) {
            LinearLayout.LayoutParams rp = new LinearLayout.LayoutParams(-1, px(38));
            if (i == 0) rp.topMargin = px(10);
            card.addView(row(jobs.get(i)), rp);
        }
        if (jobs.size() > SHOWN) {
            TextView more = text(21, 0x99FFFFFF, regular);
            more.setText("e altre " + (jobs.size() - rows) + " · premi OK per vederle tutte");
            oneLine(more);
            card.addView(more, new LinearLayout.LayoutParams(-1, px(32)));
        }
        // Only a card with jobs behind it takes focus, so OK always opens something.
        card.setFocusable(!jobs.isEmpty());
    }

    // One job on one line: NUOVA, the agency badge, the title (cut if long), and the deadline.
    private View row(Job j) {
        LinearLayout r = new LinearLayout(a);
        r.setOrientation(LinearLayout.HORIZONTAL);
        r.setGravity(Gravity.CENTER_VERTICAL);
        if (j.isNew) r.addView(pill("NUOVA", GOLD, 0xFF1A1206, 16), gap(10));
        r.addView(pill(j.badge, agencyColor(j.badge), Color.WHITE, 17), gap(14));
        TextView title = text(24, 0xEBFFFFFF, regular);
        title.setText(j.title);
        oneLine(title);
        r.addView(title, new LinearLayout.LayoutParams(0, -2, 1f));
        if (j.due != null) {
            TextView due = text(21, dueColor(j.due), j.due != null && daysLeft(j.due) <= 7 ? semibold : regular);
            due.setText(dueShort(j.due));
            LinearLayout.LayoutParams dp = new LinearLayout.LayoutParams(-2, -2);
            dp.leftMargin = px(20);
            r.addView(due, dp);
        }
        return r;
    }

    // ---- the panels ----

    // OK on the card: every job in a side panel; OK on one shows all of it, with a QR code.
    private void showList() {
        if (jobs.isEmpty()) return;
        LinearLayout list = new LinearLayout(a);
        list.setOrientation(LinearLayout.VERTICAL);
        for (final Job j : jobs) {
            LinearLayout item = new LinearLayout(a);
            item.setOrientation(LinearLayout.VERTICAL);
            item.setPadding(px(24), px(16), px(24), px(16));
            final GradientDrawable focus = new GradientDrawable();
            focus.setCornerRadius(px(26));
            focus.setColor(Color.TRANSPARENT);
            item.setBackground(focus);
            item.setFocusable(true);
            item.setOnFocusChangeListener((v, has) -> focus.setColor(has ? 0x38FFFFFF : Color.TRANSPARENT));

            LinearLayout top = new LinearLayout(a);
            top.setOrientation(LinearLayout.HORIZONTAL);
            top.setGravity(Gravity.CENTER_VERTICAL);
            if (j.isNew) top.addView(pill("NUOVA", GOLD, 0xFF1A1206, 16), gap(12));
            TextView t = text(28, Color.WHITE, semibold);
            t.setText(j.title);
            t.setMaxLines(2);
            t.setEllipsize(TextUtils.TruncateAt.END);
            top.addView(t, new LinearLayout.LayoutParams(0, -2, 1f));
            item.addView(top);

            LinearLayout sub = new LinearLayout(a);
            sub.setOrientation(LinearLayout.HORIZONTAL);
            sub.setGravity(Gravity.CENTER_VERTICAL);
            sub.addView(pill(j.badge, agencyColor(j.badge), Color.WHITE, 16), gap(14));
            TextView g = text(22, DIM, regular);
            g.setText(j.grade + (j.due != null ? "  ·  " : ""));
            sub.addView(g);
            if (j.due != null) {
                TextView d = text(22, dueColor(j.due), daysLeft(j.due) <= 7 ? semibold : regular);
                d.setText(dueLong(j.due));
                sub.addView(d);
            }
            LinearLayout.LayoutParams subp = new LinearLayout.LayoutParams(-1, -2);
            subp.topMargin = px(8);
            item.addView(sub, subp);
            item.setOnClickListener(v -> showJob(j));
            list.addView(item);
        }
        String head = filtered ? "Posizioni P3 a Roma per te" : "Posizioni P3 a Roma";
        String note = updated == null ? null : "Aggiornato " + when(updated)
                + (sitesOk > 0 ? " · " + sitesOk + " siti ONU controllati" : "");
        sidePanel(head, note, list, px(920));
    }

    private void showJob(Job j) {
        LinearLayout box = new LinearLayout(a);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(px(24), 0, px(24), px(24));

        LinearLayout tags = new LinearLayout(a);
        tags.setOrientation(LinearLayout.HORIZONTAL);
        tags.setGravity(Gravity.CENTER_VERTICAL);
        tags.addView(pill(j.badge, agencyColor(j.badge), Color.WHITE, 18), gap(12));
        if (j.isNew) tags.addView(pill("NUOVA", GOLD, 0xFF1A1206, 18), gap(12));
        for (String w : j.words) tags.addView(pill(w, 0x26FFFFFF, 0xE6FFFFFF, 18), gap(10));
        box.addView(tags);

        TextView t = text(34, Color.WHITE, semibold);
        t.setText(j.title);
        LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(-1, -2);
        tp.topMargin = px(18);
        box.addView(t, tp);

        TextView sub = text(24, 0xD9FFFFFF, regular);
        StringBuilder b = new StringBuilder();
        if (!j.badge.equals(j.agency)) b.append(j.agency).append("\n");
        b.append(j.grade);
        if (!TextUtils.isEmpty(j.location)) b.append(" · ").append(j.location);
        sub.setText(b);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(-1, -2);
        sp.topMargin = px(12);
        box.addView(sub, sp);
        if (j.due != null) {
            TextView d = text(24, dueColor(j.due), semibold);
            String left = dueShort(j.due);
            d.setText("Scade il " + new SimpleDateFormat("d MMMM yyyy", IT).format(j.due)
                    + (daysLeft(j.due) <= 7 ? " (" + left.replace("scade ", "") + ")" : ""));
            LinearLayout.LayoutParams dp = new LinearLayout.LayoutParams(-1, -2);
            dp.topMargin = px(6);
            box.addView(d, dp);
        }

        if (!TextUtils.isEmpty(j.url)) {
            LinearLayout apply = new LinearLayout(a);
            apply.setOrientation(LinearLayout.HORIZONTAL);
            apply.setGravity(Gravity.CENTER_VERTICAL);
            if (j.qr != null && !j.qr.isEmpty()) {
                QrView qr = new QrView(a, j.qr);
                int size = px(300);
                LinearLayout.LayoutParams qp = new LinearLayout.LayoutParams(size, size);
                qp.rightMargin = px(32);
                apply.addView(qr, qp);
            }
            LinearLayout words = new LinearLayout(a);
            words.setOrientation(LinearLayout.VERTICAL);
            TextView how = text(26, Color.WHITE, semibold);
            how.setText(j.qr != null && !j.qr.isEmpty() ? "Inquadra con il telefono" : "Per candidarti");
            words.addView(how);
            TextView how2 = text(22, DIM, regular);
            how2.setText(j.qr != null && !j.qr.isEmpty() ? "si apre l'offerta e ti candidi da lì."
                    : "apri questo link dal telefono o dal computer:");
            words.addView(how2);
            TextView link = text(18, 0x80FFFFFF, regular);
            link.setText(j.url);
            link.setMaxLines(4);
            link.setEllipsize(TextUtils.TruncateAt.END);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
            lp.topMargin = px(16);
            words.addView(link, lp);
            apply.addView(words, new LinearLayout.LayoutParams(0, -2, 1f));
            LinearLayout.LayoutParams ap = new LinearLayout.LayoutParams(-1, -2);
            ap.topMargin = px(36);
            box.addView(apply, ap);
        }
        box.setFocusable(true);
        sidePanel("Dettagli", null, box, px(920));
    }

    // A panel from the right, in the same dark glass as the home screen's own panels. Back closes it.
    private void sidePanel(String title, String note, View content, int width) {
        Dialog d = new Dialog(a, android.R.style.Theme_Material_Dialog_NoActionBar);
        LinearLayout box = new LinearLayout(a);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(px(48), px(80), px(48), px(40));
        TextView head = text(40, Color.WHITE, semibold);
        head.setText(title);
        head.setPadding(px(24), 0, px(24), 0);
        oneLine(head);
        box.addView(head);
        if (note != null) {
            TextView n = text(21, 0x99FFFFFF, regular);
            n.setText(note);
            n.setPadding(px(24), px(8), px(24), 0);
            oneLine(n);
            box.addView(n);
        }
        ScrollView scroll = new ScrollView(a);
        scroll.setVerticalScrollBarEnabled(false);
        scroll.addView(content);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, 0, 1f);
        lp.topMargin = px(28);
        box.addView(scroll, lp);
        d.setContentView(box);
        Window w = d.getWindow();
        w.setBackgroundDrawable(new ColorDrawable(0xF7161113));
        w.setLayout(width, -1);
        w.setGravity(Gravity.END);
        w.setDimAmount(0.5f);
        d.show();
    }

    // ---- small pieces ----

    private TextView pill(String label, int fill, int ink, int size) {
        TextView t = text(size, ink, semibold);
        t.setText(label);
        t.setLetterSpacing(0.04f);
        t.setSingleLine(true);
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(px(8));
        g.setColor(fill);
        t.setBackground(g);
        t.setPadding(px(9), px(3), px(9), px(3));
        return t;
    }

    private LinearLayout.LayoutParams gap(int right) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-2, -2);
        p.rightMargin = px(right);
        return p;
    }

    private static int agencyColor(String badge) {
        Integer c = AGENCY_COLORS.get(badge == null ? "" : badge.toUpperCase(Locale.ROOT));
        return c != null ? c : OTHER_AGENCY;
    }

    private static long daysLeft(Date due) {
        return Math.round((due.getTime() - day(System.currentTimeMillis()).getTime()) / (double) DAY_MS);
    }

    private static int dueColor(Date due) {
        long d = daysLeft(due);
        return d <= 1 ? RED : d <= 7 ? ORANGE : DIM;
    }

    // "scade oggi", "scade domani", "tra 4 giorni", "scade 12 nov"
    private static String dueShort(Date due) {
        long d = daysLeft(due);
        if (d <= 0) return "scade oggi";
        if (d == 1) return "scade domani";
        if (d <= 7) return "tra " + d + " giorni";
        return "scade " + new SimpleDateFormat("d MMM", IT).format(due);
    }

    private static String dueLong(Date due) {
        long d = daysLeft(due);
        String date = new SimpleDateFormat("d MMMM", IT).format(due);
        if (d <= 0) return "scade oggi";
        if (d == 1) return "scade domani";
        if (d <= 7) return "scade tra " + d + " giorni (" + date + ")";
        return "scade il " + date;
    }

    // "oggi alle 6:23", "ieri alle 13:20", "il 28 ottobre alle 6:23"
    private static String when(Date t) {
        Calendar c = Calendar.getInstance();
        long today = day(c.getTimeInMillis()).getTime(), that = day(t.getTime()).getTime();
        String hour = new SimpleDateFormat("H:mm", IT).format(t);
        if (that == today) return "oggi alle " + hour;
        if (today - that <= DAY_MS + 3600000L && today > that) return "ieri alle " + hour;
        return "il " + new SimpleDateFormat("d MMMM", IT).format(t) + " alle " + hour;
    }

    private static Date date(String s) {
        if (TextUtils.isEmpty(s)) return null;
        try {
            return new SimpleDateFormat("yyyy-MM-dd", Locale.ROOT).parse(s.length() > 10 ? s.substring(0, 10) : s);
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

    // A QR code drawn from jobs.json's "qr" rows (hex, 1 = dark), on white with the usual quiet zone.
    static final class QrView extends View {
        private final boolean[][] dark;
        private final Paint ink = new Paint();

        QrView(Context c, List<String> rows) {
            super(c);
            int n = rows.size();
            dark = new boolean[n][n];
            for (int y = 0; y < n; y++) {
                String row = rows.get(y);
                for (int x = 0; x < n && x / 4 < row.length(); x++) {
                    int nibble = Character.digit(row.charAt(x / 4), 16);
                    dark[y][x] = nibble >= 0 && (nibble & (8 >> (x % 4))) != 0;
                }
            }
            ink.setColor(Color.BLACK);
            ink.setAntiAlias(false);
        }

        @Override protected void onDraw(Canvas canvas) {
            int n = dark.length, size = Math.min(getWidth(), getHeight());
            canvas.drawColor(Color.WHITE);
            if (n == 0) return;
            int cell = Math.max(1, size / (n + 8)); // four modules of white all round
            int off = (size - cell * n) / 2;
            for (int y = 0; y < n; y++)
                for (int x = 0; x < n; x++)
                    if (dark[y][x]) canvas.drawRect(off + x * cell, off + y * cell,
                            off + (x + 1) * cell, off + (y + 1) * cell, ink);
        }
    }
}
