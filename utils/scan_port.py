#!/usr/bin/env python3

import argparse
import platform
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from ipaddress import ip_network


def is_host_reachable(ip: str, timeout: float) -> bool:
    system = platform.system().lower()

    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), ip]

    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return result.returncode == 0


def check_port(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_host(ip: str, ports: list[int], timeout: float):
    port_results = {}

    for port in ports:
        port_results[port] = check_port(ip, port, timeout)

    reachable = is_host_reachable(ip, timeout)
    has_open_port = any(port_results.values())

    if not reachable and not has_open_port:
        return None

    return ip, reachable, port_results


def parse_ports(raw_ports: str) -> list[int]:
    ports = []

    for item in raw_ports.split(","):
        port = int(item.strip())

        if port < 1 or port > 65535:
            raise ValueError(f"Invalid port: {port}")

        ports.append(port)

    return ports


def ip_key(ip: str):
    return tuple(map(int, ip.split(".")))


def main():
    parser = argparse.ArgumentParser(
        description="Simple TCP port scanner"
    )

    parser.add_argument(
        "subnet",
        help="Subnet to scan, e.g. 192.168.1.0/24"
    )

    parser.add_argument(
        "ports",
        help="Comma-separated list of ports, e.g. 22,80,443,502"
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=0.5,
        help="Connection timeout in seconds"
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=64,
        help="Number of concurrent workers"
    )

    args = parser.parse_args()

    ports = parse_ports(args.ports)
    hosts = [str(ip) for ip in ip_network(args.subnet, strict=False).hosts()]

    print()
    print(f"Scanning subnet: {args.subnet}")
    print(f"Ports: {ports}")
    print()

    header = f"{'IP ADDRESS':<18} {'HOST':<10}"

    for port in ports:
        header += f"{str(port):<10}"

    print(header)
    print("-" * len(header))

    results = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(scan_host, ip, ports, args.timeout)
            for ip in hosts
        ]

        for future in as_completed(futures):
            result = future.result()

            if result is not None:
                results.append(result)

    for ip, reachable, port_results in sorted(results, key=lambda x: ip_key(x[0])):
        line = f"{ip:<18} {'UP' if reachable else 'UNKNOWN':<10}"

        for port in ports:
            status = "OPEN" if port_results[port] else "CLOSED"
            line += f"{status:<10}"

        print(line)


if __name__ == "__main__":
    main()
