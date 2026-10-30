#!/bin/sh
# Installs the home screen on an Android TV (real or emulator) exactly like a user would,
# then checks it works. Usage: sh ci/smoke-test.sh <adb serial or TV_IP> [screenshot.png]
set -e
cd "$(dirname "$0")/.."
TV="$1"; SHOT="${2:-smoke.png}"
fail() {
  echo "FAIL: $*" >&2
  echo "-- on screen: $(focus)" >&2
  adb -s "$TV" logcat -d -b crash 2>/dev/null | grep -A12 "FATAL EXCEPTION" | head -40 >&2
  adb -s "$TV" exec-out screencap -p > "${SHOT%.png}-fail.png" 2>/dev/null || true
  exit 1
}
# Waits up to 15s for the home screen to be in front.
wait_home() { for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do focus | grep -q com.ansub.tvhome && return 0; sleep 1; done; return 1; }
ui() { adb -s "$TV" shell "uiautomator dump /sdcard/ui.xml" >/dev/null 2>&1; adb -s "$TV" shell "cat /sdcard/ui.xml"; }
focus() { adb -s "$TV" shell dumpsys window | grep -m1 mCurrentFocus; }

echo "== Android $(adb -s "$TV" shell getprop ro.build.version.release | tr -d '\r'), $(adb -s "$TV" shell wm size | tail -1 | tr -d '\r')"

echo "== install"
YES=1 sh install.sh "$TV"
sleep 3
home=$(adb -s "$TV" shell cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME | tail -1 | tr -d '\r')
case "$home" in
  com.ansub.tvhome/.MainActivity|org.droidtv.homeintentresolver/*) ;;
  *) fail "default home is $home" ;;
esac
adb -s "$TV" shell input keyevent KEYCODE_HOME
wait_home || fail "home screen not in front"

echo "== home screen shows a clock and Settings"
x=$(ui)
echo "$x" | grep -q 'text="Settings"' || fail "no Settings tile"
echo "$x" | grep -qE 'text="[0-9]{1,2}:[0-9]{2}"' || fail "no clock"
tiles=$(echo "$x" | grep -o 'focusable="true"' | wc -l | tr -d ' ')
echo "   $tiles tiles"
if [ "$FAKE_INPUT" = 1 ]; then
  echo "== emulator input registered"
  # Its tile may already have a saved name from a previous manual test.
  adb -s "$TV" shell dumpsys tv_input | grep -q 'com.ansub.fakeinput/.FakeInputService' \
    || fail "fake input not registered"
fi

echo "== remote: move right, open Settings, come back with Home"
adb -s "$TV" shell input keyevent KEYCODE_DPAD_RIGHT
adb -s "$TV" shell am start -a android.settings.SETTINGS >/dev/null; sleep 3
adb -s "$TV" shell input keyevent KEYCODE_HOME
wait_home || fail "Home didn't come back"

echo "== custom app list"
# Use whatever TV app this device has (test images may have none).
app=$(adb -s "$TV" shell cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.LEANBACK_LAUNCHER \
  | tr -d '\r' | grep / | sed 's|/.*||; s/^ *//' | grep -vx com.ansub.tvhome | head -1)
if [ -n "$app" ]; then
  YES=1 sh install.sh "$TV" "$app"
  adb -s "$TV" shell input keyevent KEYCODE_HOME
  wait_home || fail "home screen not in front after custom list"
  n=$(ui | grep -o 'focusable="true"' | wc -l | tr -d ' ')
  [ "$n" -ge 2 ] || fail "custom app $app didn't show up"
  echo "   $app shows up"
else
  echo "   skipped: this device has no TV apps"
fi

adb -s "$TV" exec-out screencap -p > "$SHOT"

# The greeting card, in every state. Sample feeds are served from this machine through adb reverse
# (the app allows plain HTTP only to 127.0.0.1), with deadlines counted from today.
if [ "$JOBS_SAMPLES" = 1 ]; then
  echo "== the jobs card"
  feeds=$(mktemp -d)
  python3 ci/make-sample-jobs.py "$feeds" >/dev/null
  python3 -m http.server 8765 --bind 127.0.0.1 --directory "$feeds" >/dev/null 2>&1 &
  server=$!
  adb -s "$TV" reverse tcp:8765 tcp:8765 >/dev/null
  url=http://127.0.0.1:8765
  shot() { adb -s "$TV" exec-out screencap -p > "${SHOT%.png}-jobs-$1.png"; }
  # Waits up to 30s for a text on screen; the card loads its feed on a background thread.
  wait_text() { for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do ui | grep -q "$1" && return 0; sleep 2; done; return 1; }
  feed() {
    adb -s "$TV" shell am start -n com.ansub.tvhome/.MainActivity --es stamp "$(date +%s%N)" \
      --es feed "$url/$1.json" >/dev/null
  }
  focused() { ui | grep -o '<node[^>]*content-desc="Offerte di lavoro"[^>]*>' | grep -q 'focused="true"'; }

  GREET=Test KEYWORDS="innovation,transformation,change,management,strategy" FEED="$url/many.json" \
    YES=1 sh install.sh "$TV" >/dev/null
  adb -s "$TV" shell input keyevent KEYCODE_HOME
  wait_home || fail "home screen not in front after turning on the jobs card"
  wait_text 'text="Ciao Test,"' || fail "no jobs card"
  wait_text '5 posizioni P3 a Roma per te' || fail "the card didn't show the 5 matching jobs"
  ui | grep -q 'Climate Change Officer' && fail "a job without the keywords is on the card"
  echo "   5 jobs for the keywords, the others left out"
  shot many

  # Up from the apps reaches the card; OK opens the list, OK on a job its details with the QR code.
  for _ in 1 2 3; do focused && break; adb -s "$TV" shell input keyevent KEYCODE_DPAD_UP; sleep 1; done
  if focused; then
    adb -s "$TV" shell input keyevent KEYCODE_DPAD_CENTER
    wait_text 'Posizioni P3 a Roma per te' || fail "OK on the card didn't open the list"
    ui | grep -q 'Climate Change Officer' && fail "a job without the keywords is in the list"
    shot list
    adb -s "$TV" shell input keyevent KEYCODE_DPAD_CENTER
    wait_text 'Inquadra con il telefono' || fail "OK on a job didn't open its details with a QR code"
    shot detail
    adb -s "$TV" shell input keyevent KEYCODE_BACK; sleep 1
    adb -s "$TV" shell input keyevent KEYCODE_BACK; sleep 1
    echo "   list and details open, with a QR code"
  else
    echo "   couldn't move the focus to the card: list and details not checked"
  fi

  feed one
  wait_text "1 posizione P3 a Roma per te" || fail "the card didn't show the one matching job"
  shot one
  feed empty
  wait_text 'nessuna posizione P3 a Roma per te' || fail "the card didn't show the empty state"
  wait_text 'Controllo 11 siti ONU' || fail "the empty card doesn't say the search goes on"
  shot empty
  echo "   one job and no jobs shown right"

  kill "$server" 2>/dev/null || true
  adb -s "$TV" reverse --remove tcp:8765 >/dev/null 2>&1 || true
fi

echo "== check.sh refuses a list it can't read"
list=$(mktemp)
echo "# nothing to check" > "$list"
sh check.sh "$TV" "$list" 2>&1 | grep -q "Can't read" && fail "check.sh refused a list it can read"
rm -f "$list"
rc=0; sh check.sh "$TV" "$list" >/dev/null 2>&1 || rc=$?
[ "$rc" = 2 ] || fail "check.sh exit $rc for a list it can't read, expected 2"

echo "== undo puts the TV back"
sh undo.sh "$TV" >/dev/null
if [ "$FAKE_INPUT" = 1 ]; then
  if adb -s "$TV" shell pm list packages com.ansub.fakeinput | grep -q 'package:com.ansub.fakeinput'; then
    fail "undo left the fake input installed"
  fi
fi
sleep 3
home=$(adb -s "$TV" shell cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME | tail -1 | tr -d '\r')
case "$home" in *com.ansub.tvhome*|"") fail "after undo, home is '$home'" ;; esac
echo "   home is back to $home"
echo "PASS ($SHOT)"
