import pandas as pd
import subprocess
import signal
import time
import re
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, render_template_string
from threading import Thread

# Initialize empty DataFrames to store the metrics
node_metrics_df = pd.DataFrame(columns=["timestamp", "node", "cpu(cores)", "memory(bytes)"])
pod_metrics_df = pd.DataFrame(columns=["timestamp", "pod", "namespace", "cpu(cores)", "memory(bytes)", "image"])

def collect_metrics():
    while True:
        timestamp = time.time()
        # print(f"Timestamp: {timestamp}")
        with ThreadPoolExecutor() as executor:
            node_future = executor.submit(collect_node_metrics)
            pod_future = executor.submit(collect_pod_metrics)
            node_output = node_future.result()
            pod_output = pod_future.result()
        if node_output:
            parse_node_metrics(node_output, timestamp)
        if pod_output:
            parse_pod_metrics(pod_output, timestamp)
        time.sleep(1)

# Flask app for serving the metrics report
def create_app():
    app = Flask(__name__)

    collector_thread = Thread(target=collect_metrics)
    collector_thread.daemon = True
    collector_thread.start()

    @app.route("/")
    def serve_report():
        try:
            with open("./kube_trim/report.html", "r") as f:
                return render_template_string(f.read())
        except Exception as e:
            return str(e)


    @app.route("/report", methods=["GET"])
    def get_report():
        report = []
        try:
            if not pod_metrics_df.empty:
                for image in pod_metrics_df["image"].unique():
                    image_data = pod_metrics_df[pod_metrics_df["image"] == image]
                    avg_cpu = image_data['cpu(cores)'].mean()
                    max_cpu = image_data['cpu(cores)'].max()
                    avg_memory = image_data['memory(bytes)'].mean()
                    max_memory = image_data['memory(bytes)'].max()
                    allocated_memory = lookup_allocated_memory(image_data.iloc[0]['namespace'], image_data.iloc[0]['pod'])
                    recommended_cpu = avg_cpu / 0.9 if avg_cpu > 0 else 1
                    recommended_memory = max_memory / 0.9 if max_memory > 0 else 1
                    cpu_over_provisioned = max_cpu / recommended_cpu if recommended_cpu > 0 else 0
                    memory_over_provisioned = allocated_memory / recommended_memory if recommended_memory > 0 else 0
                    report.append({
                        "image": image,
                        "avg_cpu(mCores)": avg_cpu,
                        "max_cpu(mCores)": max_cpu,
                        "avg_memory(Mi)": avg_memory,
                        "max_memory(Mi)": max_memory,
                        "requested_cpu(mCores)": max_cpu,  # Placeholder for requested CPU (should be updated with real data)
                        "requested_memory(Mi)": allocated_memory,
                        "recommended_cpu(mCores)": recommended_cpu,
                        "recommended_memory(Mi)": recommended_memory,
                        "cpu_over_provisioned": cpu_over_provisioned,
                        "memory_over_provisioned": memory_over_provisioned
                    })
            return jsonify(report)
        except Exception as e:
            return str(e)
    @app.route("/metrics", methods=["GET"])
    def get_metrics():
        response = {
            "node_metrics": node_metrics_df.to_dict(orient="records"),
            "pod_metrics": pod_metrics_df.to_dict(orient="records")
        }
        return jsonify(response)
    
    return app

app = create_app()

# Signal handler to stop the script gracefully
def signal_handler(sig, frame):
    print("\nStopping data collection and summarizing metrics...")
    summarize_metrics()
    exit(0)

signal.signal(signal.SIGINT, signal_handler)
print("Collecting kubectl top metrics. Press Ctrl+C to stop.")

def collect_node_metrics():
    try:
        # print("Collecting node metrics...")
        result = subprocess.run(["kubectl", "top", "nodes"], capture_output=True, text=True, check=True)
        # print("Node metrics collected successfully.")
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error collecting node metrics: {e}")
        print(f"Subprocess output: {e.stderr if hasattr(e, 'stderr') else 'No output'}")
        return ""

def collect_pod_metrics():
    try:
        # print("Collecting pod metrics...")
        result = subprocess.run(["kubectl", "top", "pods", "--all-namespaces"], capture_output=True, text=True, check=True)
        # print("Pod metrics collected successfully.")
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error collecting pod metrics: {e}")
        print(f"Subprocess output: {e.stderr if hasattr(e, 'stderr') else 'No output'}")
        return ""


def parse_node_metrics(output, timestamp):
    global node_metrics_df
    # print("Parsing node metrics...")
    lines = output.strip().split("\n")
    parsed_data = []
    for line in lines[1:]:
        parts = re.split(r'\s+', line)
        if len(parts) >= 3:
            node, cpu, memory = parts[0], parts[1], parts[2]
            # print(f"Parsed node: {node}, CPU: {cpu}, Memory: {memory}")
            cpu = int(cpu.replace("m", "").replace("%", "")) if "m" in cpu else int(cpu.replace("%", ""))
            memory = int(memory.replace("Mi", "")) * 1024 if 'Mi' in memory else int(memory.replace("Gi", "")) * 1024 * 1024 if 'Gi' in memory else int(memory.replace("Ki", "")) if 'Ki' in memory else int(memory.replace('%', ''))
            parsed_data.append([timestamp, node, cpu, memory])
    node_metrics_df = pd.concat([node_metrics_df, pd.DataFrame(parsed_data, columns=node_metrics_df.columns)], ignore_index=True)
    # print("Node metrics parsing completed.")

def parse_pod_metrics(output, timestamp):
    global pod_metrics_df
    # print("Parsing pod metrics...")
    lines = output.strip().split("\n")
    parsed_data = []
    with ThreadPoolExecutor() as executor:
        futures = []
        for line in lines[1:]:
            parts = re.split(r'\s+', line)
            if len(parts) >= 5:
                namespace, pod, cpu, memory = parts[0], parts[1], parts[2], parts[3]
                futures.append(executor.submit(process_pod_metrics, timestamp, namespace, pod, cpu, memory))
        for future in futures:
            result = future.result()
            if result:
                parsed_data.append(result)
    pod_metrics_df = pd.concat([pod_metrics_df, pd.DataFrame(parsed_data, columns=pod_metrics_df.columns)], ignore_index=True)
    # print("Pod metrics parsing completed.")

def process_pod_metrics(timestamp, namespace, pod, cpu, memory):
    try:
        # print(f"Parsed pod: {pod} (Namespace: {namespace}), CPU: {cpu}, Memory: {memory}")
        cpu = int(cpu.replace("m", "").replace("%", "")) if "m" in cpu else int(cpu.replace("%", ""))
        memory = int(memory.replace("Mi", "")) if 'Mi' in memory else int(memory.replace("Gi", "")) * 1024 if 'Gi' in memory else int(memory.replace("Ki", "")) // 1024 if 'Ki' in memory else int(memory.replace('%', ''))
        image = lookup_pod_image(namespace, pod)
        return [timestamp, pod, namespace, cpu, memory, image]
    except Exception as e:
        print(f"Error processing pod {pod} in namespace {namespace}: {e}")
        return None

def lookup_allocated_memory(namespace, pod):
    try:
        result = subprocess.run(["kubectl", "get", "pod", pod, "-n", namespace, "-o", "jsonpath={.spec.containers[*].resources.requests.memory}"], capture_output=True, text=True, check=True)
        allocated_memory = result.stdout.strip()
        if not allocated_memory:
            return 0
        allocated_memory_list = allocated_memory.split()
        allocated_memory = allocated_memory_list[0] if len(allocated_memory_list) > 0 else '0'
        if 'Mi' in allocated_memory:
            return int(allocated_memory.replace('Mi', ''))
        elif 'Gi' in allocated_memory:
            return int(allocated_memory.replace('Gi', '')) * 1024
        elif 'Ki' in allocated_memory:
            return int(allocated_memory.replace('Ki', '')) // 1024
        else:
            return int(allocated_memory)
    except subprocess.CalledProcessError as e:
        print(f"Error fetching allocated memory for pod {pod} in namespace {namespace}: {e}")
        return 0

def lookup_pod_image(namespace, pod):
    try:
        result = subprocess.run(["kubectl", "get", "pod", pod, "-n", namespace, "-o", "jsonpath={.spec.containers[*].image}"], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        # print(f"Error fetching image for pod {pod} in namespace {namespace}: {e}")
        return "unknown"

def summarize_metrics():
    print("Summarizing metrics...")
    if node_metrics_df.empty and pod_metrics_df.empty:
        print("No metrics collected.")
        return

    if not node_metrics_df.empty:
        for node in node_metrics_df["node"].unique():
            node_data = node_metrics_df[node_metrics_df["node"] == node]
            print(f"\nSummary for Node: {node}")
            print(f"CPU (mCores) - Min: {node_data['cpu(cores)'].min()}, Max: {node_data['cpu(cores)'].max()}, Avg: {node_data['cpu(cores)'].mean()} mCores")
            print(f"Memory (Mi) - Min: {node_data['memory(bytes)'].min()}, Max: {node_data['memory(bytes)'].max()}, Avg: {node_data['memory(bytes)'].mean()} Mi")

    if not pod_metrics_df.empty:
        for image in pod_metrics_df["image"].unique():
            image_data = pod_metrics_df[pod_metrics_df["image"] == image]
            max_cpu = image_data['cpu(cores)'].max()
            max_memory = image_data['memory(bytes)'].max()
            print(f"\nSummary for Image: {image}")
            print(f"  CPU (mCores) - Max: {max_cpu} mCores")
            print(f"  Memory (Mi) - Max: {max_memory} Mi")
            if max_cpu > 0:
                recommended_cpu = max_cpu / 0.9
                print(f"  Recommended CPU Request: {recommended_cpu:.2f} mCores for 90% utilization")
            if max_memory > 0:
                allocated_memory = lookup_allocated_memory(image_data.iloc[0]['namespace'], image_data.iloc[0]['pod'])
                memory_utilization = (max_memory / allocated_memory) * 100 if allocated_memory > 0 else 0
                recommended_memory = allocated_memory * 0.9 if allocated_memory > 0 else max_memory / 0.9
                print(f"  Memory Utilization: {memory_utilization:.2f}%")
                print(f"  Recommended Memory Request: {recommended_memory:.2f} Mi for 90% utilization")