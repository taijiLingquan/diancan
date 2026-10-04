"""
打印模块 - 支持多种云打印平台与局域网直连打印
用法:
    from printer import Printer
    p = Printer(config)
    p.print_receipt(order)
"""
import hashlib
import time
import requests
import socket
import json


class Printer:
    def __init__(self, config):
        self.cfg = config.get("printer", {})
        self.provider = self.cfg.get("provider", "feieyun")

    # ---------- 对外主入口 ----------
    def print_receipt(self, order):
        if self.provider == "none":
            return {"ok": True, "msg": "打印已关闭（测试模式）"}
        text = self._build_receipt_text(order)
        try:
            if self.provider == "feieyun":
                return self._print_feieyun(text)
            elif self.provider == "direct":
                return self._print_direct(text)
            else:
                return {"ok": False, "msg": f"未知打印 provider: {self.provider}"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    # ---------- 小票文本模板 ----------
    def _build_receipt_text(self, order):
        shop = order.get("shop_name", "餐厅")
        lines = []
        lines.append("<C><B>" + shop + "</B></C>")
        lines.append("<C>订单小票</C>")
        lines.append("--------------------------------")
        lines.append(f"订单号: {order['order_no']}")
        lines.append(f"下单时间: {order['created_at']}")
        lines.append(f"桌号/备注: {order.get('table_no', '堂食')} {order.get('note','')}")
        lines.append("--------------------------------")
        lines.append(f"{'商品':<12}{'数量':>4}{'金额':>8}")
        for item in order["items"]:
            name = item["name"][:12]
            qty = item["qty"]
            price = item["price"] * qty
            lines.append(f"{name:<12}{qty:>4}{price:>8.2f}")
        lines.append("--------------------------------")
        lines.append(f"{'合计':>16} ￥{order['total']:.2f}")
        lines.append(f"{'支付方式':>10} {order.get('pay_method','在线支付')}")
        lines.append("--------------------------------")
        lines.append("<C>谢谢惠顾，欢迎下次光临</C>")
        lines.append("<C>*** 取餐号 ***</C>")
        lines.append(f"<C><B>{order['order_no'][-4:]}</B></C>")
        lines.append("\n\n\n")
        return "\n".join(lines)

    # ---------- 飞鹅云 API ----------
    def _print_feieyun(self, content):
        feie = self.cfg["feieyun"]
        if not feie.get("user") or not feie.get("sn"):
            return {"ok": False, "msg": "飞鹅云未配置 user/sn"}
        url = "https://api.feieyun.cn/Api/Open/"
        now = str(int(time.time()))
        sqlist = json.dumps([{
            "user": feie["user"],
            "ptype": feie.get("print_num", 1),
            "sn": feie["sn"],
            "content": content,
            "mode": 0
        }], ensure_ascii=False)
        data = {
            "user": feie["user"],
            "stime": now,
            "sig": self._feie_sign(feie["user"], feie["ukey"], now),
            "apiname": "Open_printMsg",
            "sn": feie["sn"],
            "content": content,
            "times": feie.get("print_num", 1)
        }
        r = requests.post(url, data=data, timeout=10)
        res = r.json()
        return {"ok": res.get("ret") == 0, "raw": res}

    @staticmethod
    def _feie_sign(user, ukey, stime):
        s = f"{user}{stime}{ukey}"
        return hashlib.sha1(s.encode()).hexdigest()

    # ---------- 局域网直连 ESC/POS ----------
    def _print_direct(self, content):
        d = self.cfg["direct"]
        host = d.get("host")
        if not host:
            return {"ok": False, "msg": "直连模式未配置 host"}
        port = int(d.get("port", 9100))
        # 简易 ESC/POS 文本发送（58mm 打印机通常直接发 UTF-8 文本即可）
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))
        # 初始化 + 切纸/走纸
        payload = b"\x1b\x40"  # ESC @ 初始化
        payload += content.encode("utf-8", errors="replace")
        payload += b"\x0a\x0a\x0a\x1d\x56\x00"  # 走纸 + 切纸
        sock.sendall(payload)
        sock.close()
        return {"ok": True, "msg": "已发送到局域网打印机"}
