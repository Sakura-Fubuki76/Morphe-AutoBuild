#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import zipfile


PACKAGE_NAME = "com.google.android.youtube"
MODULE_DATA_DIR = "/data/adb/youtube_morphe"


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def add_to_zip(zf, path, arcname):
    info = zipfile.ZipInfo.from_file(path, arcname)
    name = os.path.basename(path)
    if name.endswith(".sh") or name == "update-binary" or name in ("cmpr", "ksu_profile"):
        info.external_attr = 0o755 << 16
    with open(path, "rb") as f:
        zf.writestr(info, f.read(), zipfile.ZIP_DEFLATED)


CUSTOMIZE_SH = """\
. "$MODPATH/config"

ui_print ""
if [ -n "$MODULE_ARCH" ] && [ "$MODULE_ARCH" != "$ARCH" ]; then
	abort "错误: 架构不匹配。设备: $ARCH；模块: $MODULE_ARCH"
fi
if [ "$ARCH" = "arm" ]; then
	ARCH_LIB=armeabi-v7a
elif [ "$ARCH" = "arm64" ]; then
	ARCH_LIB=arm64-v8a
elif [ "$ARCH" = "x86" ]; then
	ARCH_LIB=x86
elif [ "$ARCH" = "x64" ]; then
	ARCH_LIB=x86_64
else abort "错误: 不支持的架构: ${ARCH}"; fi

set_perm_recursive "$MODPATH/bin" 0 0 0755 0777

if su -M -c true >/dev/null 2>/dev/null; then
	alias mm='su -M -c'
else alias mm='nsenter -t1 -m'; fi

mm grep -F "$PKG_NAME" /proc/mounts | while read -r line; do
	ui_print "* 正在卸载旧的挂载"
	mp=${line#* } mp=${mp%% *}
	mm umount -l "${mp%%\\*}"
done
am force-stop "$PKG_NAME"

pmex() {
	OP=$(pm "$@" 2>&1 </dev/null)
	RET=$?
	echo "$OP"
	return $RET
}

if ! pmex path "$PKG_NAME" >&2; then
	if pmex install-existing "$PKG_NAME" >&2; then
		pmex uninstall-system-updates "$PKG_NAME"
	fi
fi

IS_SYS=false
INS=true
if BASEPATH=$(pmex path "$PKG_NAME"); then
	echo >&2 "'$BASEPATH'"
	BASEPATH=${BASEPATH##*:} BASEPATH=${BASEPATH%/*}
	if [ "${BASEPATH:1:4}" != data ]; then
		ui_print "* $PKG_NAME 是系统应用"
		IS_SYS=true
	elif [ ! -f "$MODPATH/$PKG_NAME.apk" ]; then
		ui_print "* 模块中未找到官方 $PKG_NAME APK"
		VERSION=$(dumpsys package "$PKG_NAME" 2>&1 | grep -m1 versionName) VERSION="${VERSION#*=}"
		if [ "$VERSION" = "$PKG_VER" ] || [ -z "$VERSION" ]; then
			ui_print "* 跳过安装，使用当前 base.apk"
			INS=false
		else
			abort "错误: 版本不匹配。已安装: $VERSION，模块: $PKG_VER"
		fi
	elif "${MODPATH:?}/bin/$ARCH/cmpr" "$BASEPATH/base.apk" "$MODPATH/$PKG_NAME.apk"; then
		ui_print "* $PKG_NAME 已是最新版本"
		INS=false
	fi
fi

install() {
	if [ ! -f "$MODPATH/$PKG_NAME.apk" ]; then
		abort "错误: 模块中未找到官方 $PKG_NAME APK"
	fi
	ui_print "* 正在安装 $PKG_NAME ($PKG_VER)"
	install_err=""
	VERIF1=$(settings get global verifier_verify_adb_installs)
	VERIF2=$(settings get global package_verifier_enable)
	settings put global verifier_verify_adb_installs 0
	settings put global package_verifier_enable 0
	SZ=$(stat -c "%s" "$MODPATH/$PKG_NAME.apk")
	for IT in 1 2; do
		if ! SES=$(pmex install-create --user 0 -i com.android.vending -r -d -S "$SZ"); then
			ui_print "错误: 无法创建安装会话"
			install_err="$SES"
			break
		fi
		SES=${SES#*[} SES=${SES%]*}
		set_perm "$MODPATH/$PKG_NAME.apk" 1000 1000 644 u:object_r:apk_data_file:s0
		if ! op=$(pmex install-write -S "$SZ" "$SES" "$PKG_NAME.apk" "$MODPATH/$PKG_NAME.apk"); then
			ui_print "错误: 无法写入 APK"
			install_err="$op"
			break
		fi
		if ! op=$(pmex install-commit "$SES"); then
			ui_print "$op"
			if echo "$op" | grep -q -e INSTALL_FAILED_VERSION_DOWNGRADE -e INSTALL_FAILED_UPDATE_INCOMPATIBLE; then
				ui_print "* 处理安装冲突"
				pmex uninstall-system-updates "$PKG_NAME"
				if BASEPATH=$(pmex path "$PKG_NAME"); then
					BASEPATH=${BASEPATH##*:} BASEPATH=${BASEPATH%/*}
					if [ "${BASEPATH:1:4}" != data ]; then IS_SYS=true; fi
				fi
				if [ "$IS_SYS" = true ]; then
					SCNM="/data/adb/post-fs-data.d/$PKG_NAME-uninstall.sh"
					if [ -f "$SCNM" ]; then
						ui_print "* 请移除旧模块，重启后重试"
						install_err=" "
						break
					fi
					mkdir -p /data/adb/youtube_morphe/empty /data/adb/post-fs-data.d
					echo "mount -o bind /data/adb/youtube_morphe/empty $BASEPATH" >"$SCNM"
					chmod +x "$SCNM"
					ui_print "* 已创建系统应用卸载脚本"
					ui_print "* 请重启后重新安装"
					install_err=" "
					break
				else
					ui_print "* 正在卸载当前版本..."
					if ! op=$(pmex uninstall -k --user 0 "$PKG_NAME"); then
						ui_print "$op"
						if [ $IT = 2 ]; then
							install_err="错误: 卸载失败，请手动移除。"
							break
						fi
					fi
					continue
				fi
			fi
			ui_print "错误: 安装失败"
			install_err="$op"
			break
		fi
		if BASEPATH=$(pmex path "$PKG_NAME"); then
			BASEPATH=${BASEPATH##*:} BASEPATH=${BASEPATH%/*}
		else
			install_err=" "
			break
		fi
		break
	done
	settings put global verifier_verify_adb_installs "$VERIF1"
	settings put global package_verifier_enable "$VERIF2"
	if [ "$install_err" ]; then
		ui_print "$install_err"
		abort "错误: 请禁用模块，重启后手动安装 $PKG_NAME，再重新刷入模块"
	fi
}
if [ $INS = true ] && ! install; then abort; fi

BASEPATHLIB=${BASEPATH}/lib/${ARCH}
if [ $INS = true ] || [ -z "$(ls -A1 "$BASEPATHLIB" 2>/dev/null)" ]; then
	ui_print "* 正在提取原生库"
	if [ ! -d "$BASEPATHLIB" ]; then mkdir -p "$BASEPATHLIB"; else rm -f "$BASEPATHLIB"/* >/dev/null 2>&1 || :; fi
	if ! op=$(unzip -o -j "$MODPATH/$PKG_NAME.apk" "lib/${ARCH_LIB}/*" -d "$BASEPATHLIB" 2>&1); then
		ui_print "错误: 提取原生库失败"
		abort "$op"
	fi
	set_perm_recursive "${BASEPATH}/lib" 1000 1000 755 755 u:object_r:apk_data_file:s0
fi

ui_print "* 正在设置权限"
set_perm "$MODPATH/base.apk" 1000 1000 644 u:object_r:apk_data_file:s0

ui_print "* 正在挂载 $PKG_NAME"
mkdir -p "$MODULE_DATA_DIR"
MORPHE_APK_PATH="$MODULE_DATA_DIR/${MODPATH##*/}.apk"
mv -f "$MODPATH/base.apk" "$MORPHE_APK_PATH"

if ! op=$(mm mount -o bind "$MORPHE_APK_PATH" "$BASEPATH/base.apk" 2>&1); then
	ui_print "错误: 挂载失败"
	ui_print "$op"
fi
am force-stop "$PKG_NAME"
ui_print "* 正在优化 $PKG_NAME"
cmd package compile -m speed-profile -f "$PKG_NAME" >/dev/null 2>&1 || true

if [ "$KSU" ]; then
	UID=$(dumpsys package "$PKG_NAME" 2>&1 | grep -m1 uid)
	UID=${UID#*=} UID=${UID%% *}
	if [ -z "$UID" ]; then
		UID=$(dumpsys package "$PKG_NAME" 2>&1 | grep -m1 userId)
		UID=${UID#*=} UID=${UID%% *}
	fi
	if [ "$UID" ]; then
		if ! OP=$("${MODPATH:?}/bin/$ARCH/ksu_profile" "$UID" "$PKG_NAME" 2>&1); then
			ui_print "  $OP"
			ui_print "* 如果你使用的是修改版 KernelSU，"
			ui_print "  请在 root 管理器中为 $PKG_NAME 关闭「卸载模块」选项"
		fi
	else
		ui_print "警告: 无法获取 $PKG_NAME 的 UID"
	fi
fi

rm -rf "${MODPATH:?}/bin" "$MODPATH/$PKG_NAME.apk"

ui_print "* 完成。建议重启设备。"
ui_print ""
"""

SERVICE_SH = """\
#!/system/bin/sh
MODDIR=${0%/*}
. "$MODDIR/config"
MORPHE_APK_PATH="$MODULE_DATA_DIR/${MODDIR##*/}.apk"

err() {
	[ ! -f "$MODDIR/err" ] && cp "$MODDIR/module.prop" "$MODDIR/err"
	sed -i "s/^des.*/description=⚠️ 请重新安装模块: '${1}'/g" "$MODDIR/module.prop"
}

until [ "$(getprop sys.boot_completed)" = 1 ]; do sleep 1; done
until [ -d "/sdcard/Android" ]; do sleep 1; done
while
	BASEPATH=$(pm path "$PKG_NAME" 2>&1 </dev/null)
	SVCL=$?
	[ $SVCL = 20 ]
do sleep 2; done

run() {
	if [ $SVCL != 0 ]; then
		err "应用未安装"
		return
	fi
	sleep 4

	BASEPATH=${BASEPATH##*:} BASEPATH=${BASEPATH%/*}
	if [ ! -d "$BASEPATH/lib" ]; then
		err "挂载失败。KSU 上请尝试使用挂载辅助模块 (mountify, hybrid mount, meta-module)。"
		return
	fi
	VERSION=$(dumpsys package "$PKG_NAME" 2>&1 | grep -m1 versionName) VERSION="${VERSION#*=}"
	if [ "$VERSION" != "$PKG_VER" ] && [ "$VERSION" ]; then
		err "版本不匹配 (已安装:${VERSION}, 模块:${PKG_VER})"
		return
	fi
	grep "$PKG_NAME" /proc/mounts | while read -r line; do
		mp=${line#* } mp=${mp%% *}
		umount -l "${mp%%\\*}"
	done
	if ! chcon u:object_r:apk_data_file:s0 "$MORPHE_APK_PATH"; then
		err "已修补的 APK 不存在"
		return
	fi
	mount -o bind "$MORPHE_APK_PATH" "$BASEPATH/base.apk"
	am force-stop "$PKG_NAME"
	[ -f "$MODDIR/err" ] && mv -f "$MODDIR/err" "$MODDIR/module.prop"
}

run
"""

UNINSTALL_SH = """\
#!/system/bin/sh
{
	until [ "$(getprop sys.boot_completed)" = 1 ]; do sleep 1; done
	until [ -d "/sdcard/Android" ]; do sleep 1; done

	MODDIR=${0%/*}
	. "$MODDIR/config"

	rm "$MODULE_DATA_DIR/${MODDIR##*/}.apk"
	rmdir "$MODULE_DATA_DIR" >/dev/null 2>&1 || true
	rm "/data/adb/post-fs-data.d/$PKG_NAME-uninstall.sh" >/dev/null 2>&1 || true
} &
"""

UPDATE_BINARY = """\
#!/sbin/sh

#################
# Initialization
#################

umask 022

# echo before loading util_functions
ui_print() { echo "$1"; }

require_new_magisk() {
  ui_print "*******************************"
  ui_print " 请安装 Magisk v20.4+！ "
  ui_print "*******************************"
  exit 1
}

#########################
# Load util_functions.sh
#########################

OUTFD=$2
ZIPFILE=$3

mount /data 2>/dev/null

[ -f /data/adb/magisk/util_functions.sh ] || require_new_magisk
. /data/adb/magisk/util_functions.sh
[ $MAGISK_VER_CODE -lt 20400 ] && require_new_magisk

install_module
exit 0
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apk", required=True, help="Patched APK")
    p.add_argument("--stock-apk", required=True, help="Stock (unpatched) merged APK")
    p.add_argument("--module-id", required=True)
    p.add_argument("--module-name", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--metadata", required=True)
    p.add_argument("--patches-metadata", required=True)
    p.add_argument("--cli-metadata", required=True)
    p.add_argument("--bin-dir", default="", help="Directory containing bin/ (cmpr + ksu_profile)")
    p.add_argument("--version-code", required=True, help="CI date, e.g. 20260501")
    p.add_argument("--repo", default="", help="GitHub repo (owner/name) for updateJson URL")
    args = p.parse_args()

    youtube = load(args.metadata)
    patches = load(args.patches_metadata)
    cli = load(args.cli_metadata)
    version = youtube.get("version") or youtube.get("title") or "unknown"
    patch_tag = patches.get("tag_name", "unknown")
    cli_tag = cli.get("tag_name", "unknown")

    tmp = os.path.abspath(os.path.join("build", "module-work"))
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        shutil.copy2(args.apk, os.path.join(tmp, "base.apk"))
        shutil.copy2(args.stock_apk, os.path.join(tmp, f"{PACKAGE_NAME}.apk"))
        if args.bin_dir and os.path.isdir(args.bin_dir):
            shutil.copytree(args.bin_dir, os.path.join(tmp, "bin"))

        prop_lines = [
            f"id={args.module_id}",
            f"name={args.module_name}",
            f"version=v{version} (patches {patch_tag})",
            f"versionCode={args.version_code}",
            "author=桜吹雪",
            f"description=已修补的 YouTube 模块。补丁: {patch_tag}；CLI: {cli_tag}。",
        ]
        if args.repo:
            prop_lines.append(f"updateJson=https://raw.githubusercontent.com/{args.repo}/main/dist/update.json")
        prop_lines.append("")
        write(os.path.join(tmp, "module.prop"), "\n".join(prop_lines))

        write(os.path.join(tmp, "config"), "\n".join([
            f"PKG_NAME={PACKAGE_NAME}",
            f"PKG_VER={version}",
            "MODULE_ARCH=arm64",
            f"MODULE_DATA_DIR={MODULE_DATA_DIR}",
            "",
        ]))

        write(os.path.join(tmp, "customize.sh"), CUSTOMIZE_SH)
        write(os.path.join(tmp, "service.sh"), SERVICE_SH)
        write(os.path.join(tmp, "uninstall.sh"), UNINSTALL_SH)

        write(os.path.join(tmp, "META-INF", "com", "google", "android", "update-binary"), UPDATE_BINARY)
        write(os.path.join(tmp, "META-INF", "com", "google", "android", "updater-script"), "#MAGISK\n")

        os.makedirs(args.out_dir, exist_ok=True)
        out = os.path.join(args.out_dir, "youtube.zip")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(tmp):
                for name in files:
                    path = os.path.join(root, name)
                    arcname = os.path.relpath(path, tmp)
                    add_to_zip(zf, path, arcname)
        print(out)

        if args.repo:
            update_json = os.path.join(args.out_dir, "update.json")
            write(update_json, json.dumps({
                "version": f"v{version} (patches {patch_tag})",
                "versionCode": int(args.version_code),
                "zipUrl": f"https://github.com/{args.repo}/releases/latest/download/YouTube-v{version}.zip",
                "changelog": f"https://raw.githubusercontent.com/{args.repo}/main/dist/YouTube-changelog.md",
            }, indent=2) + "\n")
            print(f"updateJson: {update_json}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
