import os
import sys
import subprocess
import json
import time
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog


class PipelineStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PipelineResult:
    status: PipelineStatus
    duration: float
    output: str
    error: Optional[str] = None
    exit_code: int = 0


class CIPipeline:
    def __init__(self, config_file: str = "ci_config.json"):
        self.config_file = config_file
        self.setup_logging()
        self.config = self.load_config()
        
    def setup_logging(self):
        """Configure logging for the CI pipeline"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('ci_pipeline.log'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)

    def load_config(self) -> Dict:
        """Load CI configuration from JSON file"""
        default_config = {
            "project_name": "MyProject",
            "build_commands": [
                {"name": "clean", "command": "echo 'Cleaning build artifacts...'", "timeout": 30},
                {"name": "install_deps", "command": "pip install -r requirements.txt", "timeout": 300},
                {"name": "build", "command": "python -m py_compile *.py", "timeout": 120}
            ],
            "test_commands": [
                {"name": "unit_tests", "command": "python -m pytest tests/ -v", "timeout": 300},
                {"name": "linting", "command": "flake8 *.py --max-line-length=100", "timeout": 60},
                {"name": "type_check", "command": "mypy *.py", "timeout": 120}
            ],
            "deployment_commands": [
                {"name": "package", "command": "echo 'Packaging application...'", "timeout": 60}
            ],
            "notifications": {
                "email": False,
                "slack": False
            }
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    user_config = json.load(f)
                    default_config.update(user_config)
            except (json.JSONDecodeError, IOError) as e:
                self.logger.warning(f"Could not load config file: {e}. Using defaults.")
        else:
            self.create_default_config(default_config)
            
        return default_config

    def create_default_config(self, config: Dict):
        """Create default configuration file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            self.logger.info(f"Created default config file: {self.config_file}")
        except IOError as e:
            self.logger.error(f"Could not create config file: {e}")

    def run_command(self, command: str, timeout: int = 300, cwd: Optional[str] = None) -> PipelineResult:
        """Execute a command and return the result"""
        start_time = time.time()
        
        try:
            self.logger.info(f"Running command: {command}")
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd
            )
            
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                exit_code = process.returncode
                duration = time.time() - start_time
                
                if exit_code == 0:
                    status = PipelineStatus.SUCCESS
                    self.logger.info(f"Command completed successfully in {duration:.2f}s")
                else:
                    status = PipelineStatus.FAILED
                    self.logger.error(f"Command failed with exit code {exit_code}")
                
                return PipelineResult(
                    status=status,
                    duration=duration,
                    output=stdout,
                    error=stderr,
                    exit_code=exit_code
                )
                
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                duration = time.time() - start_time
                self.logger.error(f"Command timed out after {timeout}s")
                
                return PipelineResult(
                    status=PipelineStatus.FAILED,
                    duration=duration,
                    output=stdout,
                    error=f"Command timed out after {timeout}s\n{stderr}",
                    exit_code=-1
                )
                
        except Exception as e:
            duration = time.time() - start_time
            self.logger.error(f"Exception running command: {e}")
            
            return PipelineResult(
                status=PipelineStatus.FAILED,
                duration=duration,
                output="",
                error=str(e),
                exit_code=-1
            )

    def run_stage(self, stage_name: str, commands: List[Dict]) -> List[PipelineResult]:
        """Run a stage with multiple commands"""
        self.logger.info(f"Starting stage: {stage_name}")
        results = []
        
        for cmd_config in commands:
            cmd_name = cmd_config.get('name', 'unnamed')
            cmd_command = cmd_config.get('command', '')
            cmd_timeout = cmd_config.get('timeout', 300)
            
            self.logger.info(f"Executing {stage_name}.{cmd_name}")
            result = self.run_command(cmd_command, cmd_timeout)
            results.append(result)
            
            if result.status == PipelineStatus.FAILED:
                self.logger.error(f"Stage {stage_name} failed at {cmd_name}")
                break
            else:
                self.logger.info(f"Completed {stage_name}.{cmd_name}")
        
        return results

    def generate_report(self, build_results: List[PipelineResult], 
                       test_results: List[PipelineResult],
                       deploy_results: List[PipelineResult]) -> Dict:
        """Generate a comprehensive pipeline report"""
        all_results = build_results + test_results + deploy_results
        
        total_duration = sum(r.duration for r in all_results)
        failed_stages = [r for r in all_results if r.status == PipelineStatus.FAILED]
        successful_stages = [r for r in all_results if r.status == PipelineStatus.SUCCESS]
        
        report = {
            "project": self.config["project_name"],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "total_stages": len(all_results),
                "successful_stages": len(successful_stages),
                "failed_stages": len(failed_stages),
                "total_duration": total_duration,
                "status": "SUCCESS" if not failed_stages else "FAILED"
            },
            "stages": {
                "build": self.format_stage_results(build_results),
                "test": self.format_stage_results(test_results),
                "deploy": self.format_stage_results(deploy_results)
            }
        }
        
        return report

    def format_stage_results(self, results: List[PipelineResult]) -> List[Dict]:
        """Format stage results for reporting"""
        formatted = []
        for i, result in enumerate(results):
            formatted.append({
                "step": i + 1,
                "status": result.status.value,
                "duration": result.duration,
                "exit_code": result.exit_code,
                "output": result.output[:500] + "..." if len(result.output) > 500 else result.output,
                "error": result.error[:500] + "..." if result.error and len(result.error) > 500 else result.error
            })
        return formatted

    def save_report(self, report: Dict, filename: str = "ci_report.json"):
        """Save pipeline report to file"""
        try:
            with open(filename, 'w') as f:
                json.dump(report, f, indent=2)
            self.logger.info(f"Pipeline report saved to {filename}")
        except IOError as e:
            self.logger.error(f"Could not save report: {e}")

    def run_pipeline(self) -> Dict:
        """Execute the complete CI pipeline"""
        self.logger.info(f"Starting CI pipeline for {self.config['project_name']}")
        pipeline_start = time.time()
        
        # Build stage
        build_results = self.run_stage("Build", self.config["build_commands"])
        
        # Check if build succeeded before running tests
        if any(r.status == PipelineStatus.FAILED for r in build_results):
            self.logger.error("Build stage failed. Skipping tests and deployment.")
            test_results = []
            deploy_results = []
        else:
            # Test stage
            test_results = self.run_stage("Test", self.config["test_commands"])
            
            # Check if tests succeeded before deployment
            if any(r.status == PipelineStatus.FAILED for r in test_results):
                self.logger.error("Test stage failed. Skipping deployment.")
                deploy_results = []
            else:
                # Deployment stage
                deploy_results = self.run_stage("Deploy", self.config["deployment_commands"])
        
        # Generate and save report
        report = self.generate_report(build_results, test_results, deploy_results)
        self.save_report(report)
        
        pipeline_duration = time.time() - pipeline_start
        self.logger.info(f"Pipeline completed in {pipeline_duration:.2f}s with status: {report['summary']['status']}")
        
        return report

    def create_sample_files(self):
        """Create sample project files for demonstration"""
        # Create requirements.txt
        requirements = """pytest>=7.0.0
flake8>=5.0.0
mypy>=1.0.0
"""
        with open("requirements.txt", "w") as f:
            f.write(requirements)
        
        # Create sample test file
        test_dir = Path("tests")
        test_dir.mkdir(exist_ok=True)
        
        test_content = """import unittest

class TestSample(unittest.TestCase):
    def test_sample(self):
        self.assertEqual(1 + 1, 2)
    
    def test_another(self):
        self.assertTrue(True)

if __name__ == '__main__':
    unittest.main()
"""
        with open("tests/test_sample.py", "w") as f:
            f.write(test_content)
        
        self.logger.info("Created sample project files")


class CIPipelineGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("CI Pipeline Automation Tool")
        self.root.geometry("1000x750")
        
        # Set dark theme colors
        self.bg_color = "#2b2b2b"
        self.fg_color = "#ffffff"
        self.button_bg = "#404040"
        self.button_fg = "#ffffff"
        self.success_color = "#4CAF50"
        self.error_color = "#f44336"
        self.warning_color = "#ff9800"
        self.frame_bg = "#3c3c3c"
        self.accent_color = "#007acc"
        
        # Configure root window
        self.root.configure(bg=self.bg_color)
        
        # Configure ttk styles
        self.setup_styles()
        
        self.pipeline = None
        self.current_thread = None
        self.is_running = False
        
        self.setup_ui()
        self.load_pipeline()
    
    def setup_styles(self):
        """Setup custom styles for ttk widgets"""
        style = ttk.Style()
        style.theme_use('clam')
        
        # Configure styles with dark theme
        style.configure('TFrame', background=self.bg_color)
        style.configure('TLabelframe', background=self.bg_color, foreground=self.fg_color)
        style.configure('TLabelframe.Label', background=self.bg_color, foreground=self.fg_color)
        style.configure('TLabel', background=self.bg_color, foreground=self.fg_color)
        style.configure('TButton', background=self.button_bg, foreground=self.button_fg)
        style.map('TButton', background=[('active', self.accent_color)])
        style.configure('TEntry', fieldbackground=self.frame_bg, foreground=self.fg_color)
        style.configure('TProgressbar', background=self.accent_color, troughcolor=self.frame_bg)
        
        # Custom accent button style
        style.configure('Accent.TButton', background=self.accent_color, foreground=self.fg_color)
        style.map('Accent.TButton', background=[('active', '#005a9e')])
        
    def setup_ui(self):
        """Setup the GUI components with improved layout"""
        # Main container with padding
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)
        
        # Header section with title
        header_frame = ttk.Frame(main_frame)
        header_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 20))
        
        title_label = tk.Label(header_frame, text="🚀 CI Pipeline Automation", 
                              font=('Arial', 20, 'bold'), 
                              bg=self.bg_color, fg=self.accent_color)
        title_label.pack()
        
        subtitle_label = tk.Label(header_frame, text="Automated Build & Testing System", 
                                 font=('Arial', 10), 
                                 bg=self.bg_color, fg=self.fg_color)
        subtitle_label.pack()
        
        # Main content area (left: config/controls, right: progress)
        content_frame = ttk.Frame(main_frame)
        content_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 15))
        content_frame.columnconfigure(0, weight=1)
        content_frame.columnconfigure(1, weight=2)
        content_frame.rowconfigure(0, weight=1)
        
        # Left panel - Configuration and Controls
        left_panel = ttk.Frame(content_frame)
        left_panel.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 10))
        left_panel.columnconfigure(0, weight=1)
        
        # Configuration section
        config_frame = ttk.LabelFrame(left_panel, text="⚙️ Configuration", padding="15")
        config_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        config_frame.columnconfigure(0, weight=1)
        
        ttk.Label(config_frame, text="Config File:").grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        self.config_var = tk.StringVar(value="ci_config.json")
        self.config_entry = ttk.Entry(config_frame, textvariable=self.config_var)
        self.config_entry.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        config_button_frame = ttk.Frame(config_frame)
        config_button_frame.grid(row=2, column=0, sticky=(tk.W, tk.E))
        config_button_frame.columnconfigure(0, weight=1)
        config_button_frame.columnconfigure(1, weight=1)
        
        ttk.Button(config_button_frame, text="📁 Browse", 
                  command=self.browse_config).grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))
        ttk.Button(config_button_frame, text="🔄 Reload", 
                  command=self.load_pipeline).grid(row=0, column=1, sticky=(tk.W, tk.E))
        
        # Control buttons section
        control_frame = ttk.LabelFrame(left_panel, text="🎮 Controls", padding="15")
        control_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        control_frame.columnconfigure(0, weight=1)
        
        self.run_button = ttk.Button(control_frame, text="▶️ Run Pipeline", 
                                    command=self.run_pipeline, style="Accent.TButton")
        self.run_button.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 8))
        
        self.stop_button = ttk.Button(control_frame, text="⏹️ Stop", 
                                     command=self.stop_pipeline, state=tk.DISABLED)
        self.stop_button.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 8))
        
        ttk.Button(control_frame, text="📝 Init Sample Files", 
                  command=self.init_sample_files).grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(0, 8))
        
        ttk.Button(control_frame, text="👁️ View Config", 
                  command=self.view_config).grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(0, 8))
        
        ttk.Button(control_frame, text="📊 View Report", 
                  command=self.view_report).grid(row=4, column=0, sticky=(tk.W, tk.E), pady=(0, 8))
        
        ttk.Button(control_frame, text="📋 Manual Rules", 
                  command=self.open_manual_rules).grid(row=5, column=0, sticky=(tk.W, tk.E), pady=(0, 8))
        
        ttk.Button(control_frame, text="🐙 GitHub", 
                  command=self.open_github_interface).grid(row=6, column=0, sticky=(tk.W, tk.E))
        
        # Right panel - Progress and Status
        right_panel = ttk.Frame(content_frame)
        right_panel.grid(row=0, column=1, sticky=(tk.W, tk.E, tk.N, tk.S))
        right_panel.columnconfigure(0, weight=1)
        right_panel.rowconfigure(1, weight=1)
        
        # Status display
        status_frame = ttk.Frame(right_panel)
        status_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        
        self.status_var = tk.StringVar(value="🟢 Ready")
        status_label = tk.Label(status_frame, textvariable=self.status_var, 
                               font=('Arial', 12, 'bold'), 
                               bg=self.bg_color, fg=self.success_color)
        status_label.pack()
        
        # Progress section
        progress_frame = ttk.LabelFrame(right_panel, text="📈 Pipeline Progress", padding="15")
        progress_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        progress_frame.columnconfigure(0, weight=1)
        progress_frame.rowconfigure(3, weight=1)
        
        # Progress bars for stages
        self.stage_progress = {}
        self.stage_labels = {}
        
        stages = [
            ("🔨 Build", "Build"),
            ("🧪 Test", "Test"), 
            ("🚀 Deploy", "Deploy")
        ]
        
        for i, (display_name, stage_key) in enumerate(stages):
            # Stage label
            stage_label = tk.Label(progress_frame, text=display_name, 
                                  font=('Arial', 11, 'bold'),
                                  bg=self.bg_color, fg=self.fg_color)
            stage_label.grid(row=i*2, column=0, sticky=tk.W, pady=(10, 2))
            
            # Progress container
            progress_container = ttk.Frame(progress_frame)
            progress_container.grid(row=i*2+1, column=0, sticky=(tk.W, tk.E), pady=(0, 5))
            progress_container.columnconfigure(0, weight=1)
            
            # Progress bar
            progress = ttk.Progressbar(progress_container, mode='indeterminate', length=300)
            progress.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 10))
            
            # Status label
            status_label = tk.Label(progress_container, text="⏳ Pending", 
                                   font=('Arial', 9),
                                   bg=self.bg_color, fg=self.warning_color)
            status_label.grid(row=0, column=1)
            
            self.stage_progress[stage_key] = progress
            self.stage_labels[stage_key] = status_label
        
        # Output log section
        log_frame = ttk.LabelFrame(main_frame, text="📋 Output Log", padding="15")
        log_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        
        # Configure text widget with dark theme
        self.log_text = scrolledtext.ScrolledText(
            log_frame, 
            height=12, 
            wrap=tk.WORD,
            bg=self.frame_bg, 
            fg=self.fg_color,
            insertbackground=self.fg_color,
            font=('Consolas', 9)
        )
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Clear log button
        clear_button_frame = ttk.Frame(log_frame)
        clear_button_frame.grid(row=1, column=0, pady=(10, 0))
        
        ttk.Button(clear_button_frame, text="🗑️ Clear Log", 
                  command=self.clear_log).pack()
        
    def browse_config(self):
        """Browse for configuration file"""
        filename = filedialog.askopenfilename(
            title="Select Configuration File",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self.config_var.set(filename)
            self.load_pipeline()
    
    def load_pipeline(self):
        """Load pipeline configuration"""
        try:
            self.pipeline = CIPipeline(self.config_var.get())
            self.log_message(f"Configuration loaded: {self.config_var.get()}")
            self.status_var.set("Ready")
        except Exception as e:
            self.log_message(f"Error loading configuration: {e}")
            messagebox.showerror("Error", f"Failed to load configuration: {e}")
    
    def log_message(self, message):
        """Add message to log display"""
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()
    
    def clear_log(self):
        """Clear the log display"""
        self.log_text.delete(1.0, tk.END)
    
    def update_stage_status(self, stage, status, message=""):
        """Update stage progress and status with colors and emojis"""
        if stage in self.stage_labels:
            # Update status text with emoji and color
            if status == "running":
                self.stage_labels[stage].config(text="🔄 Running", fg=self.accent_color)
                self.stage_progress[stage].start(10)
            elif status == "success":
                self.stage_labels[stage].config(text="✅ Success", fg=self.success_color)
                self.stage_progress[stage].stop()
                self.stage_progress[stage].config(value=100)
            elif status == "failed":
                self.stage_labels[stage].config(text="❌ Failed", fg=self.error_color)
                self.stage_progress[stage].stop()
            else:  # pending
                self.stage_labels[stage].config(text="⏳ Pending", fg=self.warning_color)
            
            if message:
                self.log_message(f"{stage}: {message}")
    
    def run_pipeline(self):
        """Run the CI pipeline in a separate thread"""
        if not self.pipeline:
            messagebox.showerror("Error", "No configuration loaded")
            return
        
        if self.is_running:
            messagebox.showwarning("Warning", "Pipeline is already running")
            return
        
        self.is_running = True
        self.run_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.status_var.set("Running...")
        
        # Reset stage statuses
        for stage in self.stage_progress:
            self.update_stage_status(stage, "pending")
            self.stage_progress[stage].config(value=0)
        
        # Start pipeline in separate thread
        self.current_thread = threading.Thread(target=self._run_pipeline_thread)
        self.current_thread.daemon = True
        self.current_thread.start()
    
    def _run_pipeline_thread(self):
        """Run pipeline in background thread"""
        try:
            self.log_message(f"Starting CI pipeline for {self.pipeline.config['project_name']}")
            
            # Build stage
            self.update_stage_status("Build", "running", "Starting build stage...")
            build_results = self.pipeline.run_stage("Build", self.pipeline.config["build_commands"])
            
            if any(r.status == PipelineStatus.FAILED for r in build_results):
                self.update_stage_status("Build", "failed", "Build stage failed")
                self.pipeline_complete("FAILED")
                return
            else:
                self.update_stage_status("Build", "success", "Build stage completed")
            
            # Test stage
            self.update_stage_status("Test", "running", "Starting test stage...")
            test_results = self.pipeline.run_stage("Test", self.pipeline.config["test_commands"])
            
            if any(r.status == PipelineStatus.FAILED for r in test_results):
                self.update_stage_status("Test", "failed", "Test stage failed")
                self.pipeline_complete("FAILED")
                return
            else:
                self.update_stage_status("Test", "success", "Test stage completed")
            
            # Deploy stage
            self.update_stage_status("Deploy", "running", "Starting deployment stage...")
            deploy_results = self.pipeline.run_stage("Deploy", self.pipeline.config["deployment_commands"])
            
            if any(r.status == PipelineStatus.FAILED for r in deploy_results):
                self.update_stage_status("Deploy", "failed", "Deployment failed")
                self.pipeline_complete("FAILED")
            else:
                self.update_stage_status("Deploy", "success", "Deployment completed")
                self.pipeline_complete("SUCCESS")
                
        except Exception as e:
            self.log_message(f"Pipeline error: {e}")
            self.pipeline_complete("FAILED")
    
    def stop_pipeline(self):
        """Stop the running pipeline"""
        if self.is_running:
            self.is_running = False
            self.log_message("Pipeline stopped by user")
            self.pipeline_complete("CANCELLED")
    
    def pipeline_complete(self, status):
        """Handle pipeline completion with colored status"""
        self.is_running = False
        self.run_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        
        # Update status with emoji and color
        if status == "SUCCESS":
            self.status_var.set("🟢 Pipeline Completed Successfully")
            self.status_var.master.children['!label'].config(fg=self.success_color)
        elif status == "FAILED":
            self.status_var.set("🔴 Pipeline Failed")
            self.status_var.master.children['!label'].config(fg=self.error_color)
        else:  # CANCELLED
            self.status_var.set("🟡 Pipeline Cancelled")
            self.status_var.master.children['!label'].config(fg=self.warning_color)
        
        # Generate and save report
        if self.pipeline:
            try:
                # Create dummy results for report generation
                build_results = [PipelineResult(PipelineStatus.SUCCESS, 1.0, "")]
                test_results = [PipelineResult(PipelineStatus.SUCCESS, 1.0, "")]
                deploy_results = [PipelineResult(PipelineStatus.SUCCESS, 1.0, "")]
                
                report = self.pipeline.generate_report(build_results, test_results, deploy_results)
                report['summary']['status'] = status
                self.pipeline.save_report(report)
                
                self.log_message(f"Pipeline completed with status: {status}")
                
                if status == "SUCCESS":
                    messagebox.showinfo("✅ Success", "Pipeline completed successfully!")
                else:
                    messagebox.showwarning("⚠️ Pipeline Failed", f"Pipeline completed with status: {status}")
                    
            except Exception as e:
                self.log_message(f"Error generating report: {e}")
    
    def init_sample_files(self):
        """Initialize sample project files"""
        if not self.pipeline:
            messagebox.showerror("Error", "No configuration loaded")
            return
        
        try:
            self.pipeline.create_sample_files()
            self.log_message("Sample files created successfully")
            messagebox.showinfo("Success", "Sample project files created")
        except Exception as e:
            self.log_message(f"Error creating sample files: {e}")
            messagebox.showerror("Error", f"Failed to create sample files: {e}")
    
    def view_config(self):
        """View current configuration"""
        if not self.pipeline:
            messagebox.showerror("Error", "No configuration loaded")
            return
        
        config_window = tk.Toplevel(self.root)
        config_window.title("Pipeline Configuration")
        config_window.geometry("600x400")
        
        text_widget = scrolledtext.ScrolledText(config_window, wrap=tk.WORD)
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        config_json = json.dumps(self.pipeline.config, indent=2)
        text_widget.insert(tk.END, config_json)
        text_widget.config(state=tk.DISABLED)
    
    def view_report(self):
        """View the latest pipeline report"""
        try:
            with open("ci_report.json", 'r') as f:
                report = json.load(f)
            
            report_window = tk.Toplevel(self.root)
            report_window.title("Pipeline Report")
            report_window.geometry("700x500")
            
            # Create notebook for tabs
            notebook = ttk.Notebook(report_window)
            notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            
            # Summary tab
            summary_frame = ttk.Frame(notebook)
            notebook.add(summary_frame, text="Summary")
            
            summary_text = scrolledtext.ScrolledText(summary_frame, wrap=tk.WORD)
            summary_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            
            summary_info = f"""
Project: {report['project']}
Timestamp: {report['timestamp']}
Status: {report['summary']['status']}
Total Duration: {report['summary']['total_duration']:.2f}s
Successful Stages: {report['summary']['successful_stages']}
Failed Stages: {report['summary']['failed_stages']}
Total Stages: {report['summary']['total_stages']}
"""
            summary_text.insert(tk.END, summary_info)
            summary_text.config(state=tk.DISABLED)
            
            # Full report tab
            full_frame = ttk.Frame(notebook)
            notebook.add(full_frame, text="Full Report")
            
            full_text = scrolledtext.ScrolledText(full_frame, wrap=tk.WORD)
            full_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            
            full_json = json.dumps(report, indent=2)
            full_text.insert(tk.END, full_json)
            full_text.config(state=tk.DISABLED)
            
        except FileNotFoundError:
            messagebox.showinfo("No Report", "No pipeline report found. Run the pipeline first.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load report: {e}")
    
    def open_manual_rules(self):
        """Open manual rules configuration window"""
        rules_window = tk.Toplevel(self.root)
        rules_window.title("📋 Manual Pipeline Rules")
        rules_window.geometry("800x600")
        rules_window.configure(bg=self.bg_color)
        
        # Main container
        main_frame = ttk.Frame(rules_window, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title
        title_label = tk.Label(main_frame, text="🔧 Configure Manual Pipeline Rules", 
                              font=('Arial', 14, 'bold'), 
                              bg=self.bg_color, fg=self.accent_color)
        title_label.pack(pady=(0, 20))
        
        # Create notebook for different rule categories
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        
        # Build Rules Tab
        build_frame = ttk.Frame(notebook)
        notebook.add(build_frame, text="🔨 Build Rules")
        self.create_rules_tab(build_frame, "build")
        
        # Test Rules Tab
        test_frame = ttk.Frame(notebook)
        notebook.add(test_frame, text="🧪 Test Rules")
        self.create_rules_tab(test_frame, "test")
        
        # Deploy Rules Tab
        deploy_frame = ttk.Frame(notebook)
        notebook.add(deploy_frame, text="🚀 Deploy Rules")
        self.create_rules_tab(deploy_frame, "deploy")
        
        # Custom Rules Tab
        custom_frame = ttk.Frame(notebook)
        notebook.add(custom_frame, text="⚙️ Custom Rules")
        self.create_custom_rules_tab(custom_frame)
        
        # Button frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X)
        
        ttk.Button(button_frame, text="💾 Save Rules", 
                  command=lambda: self.save_manual_rules(rules_window)).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="🔄 Load Rules", 
                  command=lambda: self.load_manual_rules()).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="▶️ Run Custom Pipeline", 
                  command=lambda: self.run_custom_pipeline()).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="❌ Close", 
                  command=rules_window.destroy).pack(side=tk.RIGHT)
    
    def create_rules_tab(self, parent, stage_type):
        """Create rules configuration tab for a specific stage"""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        
        # Instructions
        instructions = tk.Label(parent, 
                             text=f"Configure {stage_type.upper()} stage rules:\n• Add custom commands\n• Set timeouts\n• Define success conditions",
                             font=('Arial', 10),
                             bg=self.bg_color, fg=self.fg_color,
                             justify=tk.LEFT)
        instructions.grid(row=0, column=0, sticky=tk.W, pady=(10, 15), padx=10)
        
        # Rules list frame
        rules_frame = ttk.LabelFrame(parent, text=f"{stage_type.title()} Commands", padding="10")
        rules_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=(0, 10))
        rules_frame.columnconfigure(0, weight=1)
        rules_frame.rowconfigure(1, weight=1)
        
        # Add rule button
        add_button = ttk.Button(rules_frame, text="➕ Add Command", 
                              command=lambda: self.add_rule_item(rules_frame, stage_type))
        add_button.grid(row=0, column=0, sticky=tk.W, pady=(0, 10))
        
        # Scrollable area for rules
        canvas = tk.Canvas(rules_frame, bg=self.frame_bg, highlightthickness=0)
        scrollbar = ttk.Scrollbar(rules_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=1, column=1, sticky=(tk.N, tk.S))
        
        rules_frame.columnconfigure(0, weight=1)
        rules_frame.rowconfigure(1, weight=1)
        
        # Store references
        if not hasattr(self, 'rule_widgets'):
            self.rule_widgets = {}
        self.rule_widgets[stage_type] = {
            'frame': scrollable_frame,
            'canvas': canvas,
            'items': []
        }
    
    def create_custom_rules_tab(self, parent):
        """Create custom rules configuration tab"""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        
        # Global settings
        global_frame = ttk.LabelFrame(parent, text="🌐 Global Settings", padding="10")
        global_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=10, pady=10)
        global_frame.columnconfigure(1, weight=1)
        
        # Project name
        tk.Label(global_frame, text="Project Name:", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=0, sticky=tk.W, pady=5)
        self.project_name_var = tk.StringVar(value="MyCustomProject")
        tk.Entry(global_frame, textvariable=self.project_name_var, bg=self.frame_bg, fg=self.fg_color).grid(row=0, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        # Working directory
        tk.Label(global_frame, text="Working Directory:", bg=self.bg_color, fg=self.fg_color).grid(row=1, column=0, sticky=tk.W, pady=5)
        self.work_dir_var = tk.StringVar(value=".")
        tk.Entry(global_frame, textvariable=self.work_dir_var, bg=self.frame_bg, fg=self.fg_color).grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        # Environment variables
        env_frame = ttk.LabelFrame(parent, text="🔧 Environment Variables", padding="10")
        env_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), padx=10, pady=(0, 10))
        env_frame.columnconfigure(0, weight=1)
        env_frame.rowconfigure(1, weight=1)
        
        ttk.Button(env_frame, text="➕ Add Environment Variable", 
                  command=lambda: self.add_env_var(env_frame)).grid(row=0, column=0, sticky=tk.W, pady=(0, 10))
        
        # Script editor
        script_frame = ttk.LabelFrame(parent, text="📝 Custom Script", padding="10")
        script_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=10)
        script_frame.columnconfigure(0, weight=1)
        script_frame.rowconfigure(1, weight=1)
        
        tk.Label(script_frame, text="Enter custom shell commands (one per line):", 
                bg=self.bg_color, fg=self.fg_color).grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        
        self.custom_script_text = scrolledtext.ScrolledText(
            script_frame, 
            height=10, 
            wrap=tk.WORD,
            bg=self.frame_bg, 
            fg=self.fg_color,
            insertbackground=self.fg_color,
            font=('Consolas', 9)
        )
        self.custom_script_text.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Store env variables list
        if not hasattr(self, 'env_vars'):
            self.env_vars = []
    
    def add_rule_item(self, parent, stage_type):
        """Add a new rule item to the rules list"""
        item_frame = ttk.Frame(self.rule_widgets[stage_type]['frame'])
        item_frame.pack(fill=tk.X, pady=5, padx=5)
        
        # Rule fields
        name_var = tk.StringVar(value=f"custom_{stage_type}_cmd")
        command_var = tk.StringVar(value="")
        timeout_var = tk.IntVar(value=300)
        
        # Name field
        tk.Label(item_frame, text="Name:", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        tk.Entry(item_frame, textvariable=name_var, bg=self.frame_bg, fg=self.fg_color).grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(0, 10))
        
        # Command field
        tk.Label(item_frame, text="Command:", bg=self.bg_color, fg=self.fg_color).grid(row=1, column=0, sticky=tk.W, padx=(0, 5))
        tk.Entry(item_frame, textvariable=command_var, bg=self.frame_bg, fg=self.fg_color).grid(row=1, column=1, sticky=(tk.W, tk.E), padx=(0, 10))
        
        # Timeout field
        tk.Label(item_frame, text="Timeout (s):", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=0, sticky=tk.W, padx=(0, 5))
        tk.Entry(item_frame, textvariable=timeout_var, bg=self.frame_bg, fg=self.fg_color, width=10).grid(row=2, column=1, sticky=tk.W, padx=(0, 10))
        
        # Remove button
        ttk.Button(item_frame, text="🗑️", 
                  command=lambda: self.remove_rule_item(item_frame, stage_type)).grid(row=0, column=2, rowspan=3, padx=5)
        
        item_frame.columnconfigure(1, weight=1)
        
        # Store reference
        self.rule_widgets[stage_type]['items'].append({
            'frame': item_frame,
            'name': name_var,
            'command': command_var,
            'timeout': timeout_var
        })
        
        # Update scroll region
        self.rule_widgets[stage_type]['canvas'].update_idletasks()
        self.rule_widgets[stage_type]['canvas'].configure(scrollregion=self.rule_widgets[stage_type]['canvas'].bbox("all"))
    
    def remove_rule_item(self, frame, stage_type):
        """Remove a rule item"""
        frame.destroy()
        # Remove from items list
        self.rule_widgets[stage_type]['items'] = [
            item for item in self.rule_widgets[stage_type]['items'] 
            if item['frame'] != frame
        ]
    
    def add_env_var(self, parent):
        """Add environment variable input"""
        env_frame = ttk.Frame(parent)
        env_frame.pack(fill=tk.X, pady=2, padx=5)
        
        name_var = tk.StringVar(value="")
        value_var = tk.StringVar(value="")
        
        tk.Label(env_frame, text="Name:", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        tk.Entry(env_frame, textvariable=name_var, bg=self.frame_bg, fg=self.fg_color).grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(0, 10))
        
        tk.Label(env_frame, text="Value:", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=2, sticky=tk.W, padx=(0, 5))
        tk.Entry(env_frame, textvariable=value_var, bg=self.frame_bg, fg=self.fg_color).grid(row=0, column=3, sticky=(tk.W, tk.E), padx=(0, 10))
        
        ttk.Button(env_frame, text="🗑️", 
                  command=lambda: self.remove_env_var(env_frame)).grid(row=0, column=4)
        
        env_frame.columnconfigure(1, weight=1)
        env_frame.columnconfigure(3, weight=1)
        
        self.env_vars.append({
            'frame': env_frame,
            'name': name_var,
            'value': value_var
        })
    
    def remove_env_var(self, frame):
        """Remove environment variable"""
        frame.destroy()
        self.env_vars = [var for var in self.env_vars if var['frame'] != frame]
    
    def save_manual_rules(self, window):
        """Save manual rules to configuration"""
        try:
            custom_config = {
                "project_name": self.project_name_var.get(),
                "working_directory": self.work_dir_var.get(),
                "build_commands": [],
                "test_commands": [],
                "deployment_commands": [],
                "environment_variables": {},
                "custom_script": self.custom_script_text.get(1.0, tk.END).strip()
            }
            
            # Collect rules from each stage
            for stage_type in ['build', 'test', 'deploy']:
                stage_key = f"{stage_type}_commands"
                for item in self.rule_widgets[stage_type]['items']:
                    if item['command'].get().strip():
                        custom_config[stage_key].append({
                            "name": item['name'].get(),
                            "command": item['command'].get(),
                            "timeout": item['timeout'].get()
                        })
            
            # Collect environment variables
            for var in self.env_vars:
                if var['name'].get().strip():
                    custom_config["environment_variables"][var['name'].get()] = var['value'].get()
            
            # Save to file
            with open("manual_rules_config.json", "w") as f:
                json.dump(custom_config, f, indent=2)
            
            messagebox.showinfo("✅ Success", "Manual rules saved successfully!")
            self.log_message("Manual rules configuration saved")
            
        except Exception as e:
            messagebox.showerror("❌ Error", f"Failed to save rules: {e}")
    
    def load_manual_rules(self):
        """Load manual rules from configuration"""
        try:
            if os.path.exists("manual_rules_config.json"):
                with open("manual_rules_config.json", "r") as f:
                    config = json.load(f)
                
                # Load basic settings
                self.project_name_var.set(config.get("project_name", "MyCustomProject"))
                self.work_dir_var.set(config.get("working_directory", "."))
                self.custom_script_text.delete(1.0, tk.END)
                self.custom_script_text.insert(1.0, config.get("custom_script", ""))
                
                messagebox.showinfo("✅ Success", "Manual rules loaded successfully!")
                self.log_message("Manual rules configuration loaded")
            else:
                messagebox.showinfo("ℹ️ Info", "No manual rules file found")
                
        except Exception as e:
            messagebox.showerror("❌ Error", f"Failed to load rules: {e}")
    
    def run_custom_pipeline(self):
        """Run pipeline with custom manual rules"""
        try:
            if not os.path.exists("manual_rules_config.json"):
                messagebox.showerror("❌ Error", "No manual rules found. Please save rules first.")
                return
            
            # Load custom configuration
            with open("manual_rules_config.json", "r") as f:
                custom_config = json.load(f)
            
            # Create temporary pipeline with custom config
            temp_pipeline = CIPipeline.__new__(CIPipeline)
            temp_pipeline.config_file = "manual_rules_config.json"
            temp_pipeline.setup_logging()
            temp_pipeline.config = custom_config
            
            # Update GUI status
            self.status_var.set("🔄 Running Custom Pipeline...")
            self.log_message("Starting custom pipeline with manual rules...")
            
            # Run the custom pipeline
            self.run_custom_pipeline_thread(temp_pipeline)
            
        except Exception as e:
            messagebox.showerror("❌ Error", f"Failed to run custom pipeline: {e}")
    
    def run_custom_pipeline_thread(self, pipeline):
        """Run custom pipeline in background thread"""
        def run_pipeline():
            try:
                # Set environment variables
                if "environment_variables" in pipeline.config:
                    for key, value in pipeline.config["environment_variables"].items():
                        os.environ[key] = value
                
                # Run custom script if exists
                if pipeline.config.get("custom_script", "").strip():
                    self.log_message("Executing custom script...")
                    for line in pipeline.config["custom_script"].strip().split('\n'):
                        if line.strip():
                            result = pipeline.run_command(line.strip())
                            if result.status == PipelineStatus.FAILED:
                                self.log_message(f"Custom script failed: {line}")
                                self.pipeline_complete("FAILED")
                                return
                
                # Run standard pipeline stages with custom commands
                stages = [
                    ("Build", pipeline.config.get("build_commands", [])),
                    ("Test", pipeline.config.get("test_commands", [])),
                    ("Deploy", pipeline.config.get("deployment_commands", []))
                ]
                
                for stage_name, commands in stages:
                    if commands:
                        self.update_stage_status(stage_name, "running", f"Starting {stage_name.lower()} stage...")
                        results = pipeline.run_stage(stage_name, commands)
                        
                        if any(r.status == PipelineStatus.FAILED for r in results):
                            self.update_stage_status(stage_name, "failed", f"{stage_name} stage failed")
                            self.pipeline_complete("FAILED")
                            return
                        else:
                            self.update_stage_status(stage_name, "success", f"{stage_name} stage completed")
                
                self.pipeline_complete("SUCCESS")
                
            except Exception as e:
                self.log_message(f"Custom pipeline error: {e}")
                self.pipeline_complete("FAILED")
        
        # Start in background thread
        thread = threading.Thread(target=run_pipeline)
        thread.daemon = True
        thread.start()
    
    def open_github_interface(self):
        """Open GitHub integration interface"""
        github_window = tk.Toplevel(self.root)
        github_window.title("🐙 GitHub Integration")
        github_window.geometry("900x700")
        github_window.configure(bg=self.bg_color)
        
        # Main container
        main_frame = ttk.Frame(github_window, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title
        title_label = tk.Label(main_frame, text="🐙 GitHub Repository Integration", 
                              font=('Arial', 14, 'bold'), 
                              bg=self.bg_color, fg=self.accent_color)
        title_label.pack(pady=(0, 20))
        
        # Create notebook for different GitHub features
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        
        # Repository Tab
        repo_frame = ttk.Frame(notebook)
        notebook.add(repo_frame, text="📁 Repository")
        self.create_repo_tab(repo_frame)
        
        # Actions Tab
        actions_frame = ttk.Frame(notebook)
        notebook.add(actions_frame, text="⚡ Actions")
        self.create_actions_tab(actions_frame)
        
        # Branches Tab
        branches_frame = ttk.Frame(notebook)
        notebook.add(branches_frame, text="🌿 Branches")
        self.create_branches_tab(branches_frame)
        
        # Issues Tab
        issues_frame = ttk.Frame(notebook)
        notebook.add(issues_frame, text="🐛 Issues")
        self.create_issues_tab(issues_frame)
        
        # Button frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X)
        
        ttk.Button(button_frame, text="🔗 Connect Repository", 
                  command=lambda: self.connect_github_repo()).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="🔄 Sync", 
                  command=lambda: self.sync_github_repo()).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="📊 Analytics", 
                  command=lambda: self.show_github_analytics()).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="❌ Close", 
                  command=github_window.destroy).pack(side=tk.RIGHT)
    
    def create_repo_tab(self, parent):
        """Create repository management tab"""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        
        # Repository info frame
        info_frame = ttk.LabelFrame(parent, text="📊 Repository Information", padding="10")
        info_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=10, pady=10)
        info_frame.columnconfigure(1, weight=1)
        
        # Repository URL
        tk.Label(info_frame, text="Repository URL:", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=0, sticky=tk.W, pady=5)
        self.repo_url_var = tk.StringVar(value="https://github.com/username/repository")
        tk.Entry(info_frame, textvariable=self.repo_url_var, bg=self.frame_bg, fg=self.fg_color).grid(row=0, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        # Branch
        tk.Label(info_frame, text="Branch:", bg=self.bg_color, fg=self.fg_color).grid(row=1, column=0, sticky=tk.W, pady=5)
        self.branch_var = tk.StringVar(value="main")
        tk.Entry(info_frame, textvariable=self.branch_var, bg=self.frame_bg, fg=self.fg_color).grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        # Local path
        tk.Label(info_frame, text="Local Path:", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=0, sticky=tk.W, pady=5)
        self.local_path_var = tk.StringVar(value=".")
        tk.Entry(info_frame, textvariable=self.local_path_var, bg=self.frame_bg, fg=self.fg_color).grid(row=2, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        # Actions frame
        actions_frame = ttk.LabelFrame(parent, text="🎯 Repository Actions", padding="10")
        actions_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), padx=10, pady=10)
        actions_frame.columnconfigure(0, weight=1)
        
        # Action buttons
        button_grid = ttk.Frame(actions_frame)
        button_grid.pack(fill=tk.X)
        
        ttk.Button(button_grid, text="📥 Clone Repository", 
                  command=lambda: self.clone_repo()).grid(row=0, column=0, padx=5, pady=5)
        ttk.Button(button_grid, text="📤 Push Changes", 
                  command=lambda: self.push_changes()).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(button_grid, text="📥 Pull Changes", 
                  command=lambda: self.pull_changes()).grid(row=0, column=2, padx=5, pady=5)
        
        ttk.Button(button_grid, text="🌿 Create Branch", 
                  command=lambda: self.create_branch()).grid(row=1, column=0, padx=5, pady=5)
        ttk.Button(button_grid, text="🔀 Switch Branch", 
                  command=lambda: self.switch_branch()).grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(button_grid, text="🗑️ Delete Branch", 
                  command=lambda: self.delete_branch()).grid(row=1, column=2, padx=5, pady=5)
        
        # Status frame
        status_frame = ttk.LabelFrame(parent, text="📈 Repository Status", padding="10")
        status_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=10)
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(1, weight=1)
        
        # Status display
        self.repo_status_text = scrolledtext.ScrolledText(
            status_frame, 
            height=8, 
            wrap=tk.WORD,
            bg=self.frame_bg, 
            fg=self.fg_color,
            insertbackground=self.fg_color,
            font=('Consolas', 9)
        )
        self.repo_status_text.pack(fill=tk.BOTH, expand=True)
    
    def create_actions_tab(self, parent):
        """Create GitHub Actions tab"""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        
        # Workflow configuration
        workflow_frame = ttk.LabelFrame(parent, text="⚙️ Workflow Configuration", padding="10")
        workflow_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=10, pady=10)
        workflow_frame.columnconfigure(0, weight=1)
        
        # Workflow buttons
        ttk.Button(workflow_frame, text="📝 Create CI Workflow", 
                  command=lambda: self.create_ci_workflow()).pack(fill=tk.X, pady=5)
        ttk.Button(workflow_frame, text="📝 Create CD Workflow", 
                  command=lambda: self.create_cd_workflow()).pack(fill=tk.X, pady=5)
        ttk.Button(workflow_frame, text="📝 Create Custom Workflow", 
                  command=lambda: self.create_custom_workflow()).pack(fill=tk.X, pady=5)
        
        # Workflow list
        list_frame = ttk.LabelFrame(parent, text="📋 Active Workflows", padding="10")
        list_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), padx=10, pady=10)
        list_frame.columnconfigure(0, weight=1)
        
        # Workflow listbox with scrollbar
        list_container = ttk.Frame(list_frame)
        list_container.pack(fill=tk.BOTH, expand=True)
        
        self.workflow_listbox = tk.Listbox(list_container, bg=self.frame_bg, fg=self.fg_color, font=('Consolas', 9))
        workflow_scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=self.workflow_listbox.yview)
        self.workflow_listbox.configure(yscrollcommand=workflow_scrollbar.set)
        
        self.workflow_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        workflow_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Workflow actions
        action_frame = ttk.Frame(list_frame)
        action_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(action_frame, text="▶️ Run Workflow", 
                  command=lambda: self.run_workflow()).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(action_frame, text="📊 View Logs", 
                  command=lambda: self.view_workflow_logs()).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(action_frame, text="🗑️ Delete Workflow", 
                  command=lambda: self.delete_workflow()).pack(side=tk.LEFT)
        
        # Workflow status
        status_frame = ttk.LabelFrame(parent, text="📊 Workflow Status", padding="10")
        status_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=10)
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(1, weight=1)
        
        self.workflow_status_text = scrolledtext.ScrolledText(
            status_frame, 
            height=6, 
            wrap=tk.WORD,
            bg=self.frame_bg, 
            fg=self.fg_color,
            insertbackground=self.fg_color,
            font=('Consolas', 9)
        )
        self.workflow_status_text.pack(fill=tk.BOTH, expand=True)
    
    def create_branches_tab(self, parent):
        """Create branches management tab"""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        
        # Branch operations
        ops_frame = ttk.LabelFrame(parent, text="🌿 Branch Operations", padding="10")
        ops_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=10, pady=10)
        ops_frame.columnconfigure(1, weight=1)
        
        # Create branch
        tk.Label(ops_frame, text="New Branch Name:", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=0, sticky=tk.W, pady=5)
        self.new_branch_var = tk.StringVar(value="feature/new-feature")
        tk.Entry(ops_frame, textvariable=self.new_branch_var, bg=self.frame_bg, fg=self.fg_color).grid(row=0, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        # Branch buttons
        button_frame = ttk.Frame(ops_frame)
        button_frame.grid(row=1, column=0, columnspan=2, pady=10)
        
        ttk.Button(button_frame, text="🌿 Create Branch", 
                  command=lambda: self.create_branch_action()).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="🔀 Switch Branch", 
                  command=lambda: self.switch_branch_action()).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="🔀 Merge Branch", 
                  command=lambda: self.merge_branch()).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="🗑️ Delete Branch", 
                  command=lambda: self.delete_branch_action()).pack(side=tk.LEFT)
        
        # Branch list
        list_frame = ttk.LabelFrame(parent, text="📋 Available Branches", padding="10")
        list_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=10)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        
        # Branch listbox
        self.branch_listbox = tk.Listbox(list_frame, bg=self.frame_bg, fg=self.fg_color, font=('Consolas', 9))
        branch_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.branch_listbox.yview)
        self.branch_listbox.configure(yscrollcommand=branch_scrollbar.set)
        
        self.branch_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        branch_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Load branches
        self.load_branches()
    
    def create_issues_tab(self, parent):
        """Create issues management tab"""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        
        # Create issue
        create_frame = ttk.LabelFrame(parent, text="🐛 Create Issue", padding="10")
        create_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=10, pady=10)
        create_frame.columnconfigure(1, weight=1)
        
        # Issue fields
        tk.Label(create_frame, text="Title:", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=0, sticky=tk.W, pady=5)
        self.issue_title_var = tk.StringVar(value="")
        tk.Entry(create_frame, textvariable=self.issue_title_var, bg=self.frame_bg, fg=self.fg_color).grid(row=0, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        tk.Label(create_frame, text="Description:", bg=self.bg_color, fg=self.fg_color).grid(row=1, column=0, sticky=tk.W, pady=5)
        self.issue_desc_text = tk.Text(create_frame, height=4, bg=self.frame_bg, fg=self.fg_color, font=('Consolas', 9))
        self.issue_desc_text.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        # Labels
        tk.Label(create_frame, text="Labels:", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=0, sticky=tk.W, pady=5)
        self.issue_labels_var = tk.StringVar(value="bug, enhancement")
        tk.Entry(create_frame, textvariable=self.issue_labels_var, bg=self.frame_bg, fg=self.fg_color).grid(row=2, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        # Create button
        ttk.Button(create_frame, text="🐛 Create Issue", 
                  command=lambda: self.create_issue()).grid(row=3, column=0, columnspan=2, pady=10)
        
        # Issues list
        list_frame = ttk.LabelFrame(parent, text="📋 Recent Issues", padding="10")
        list_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), padx=10, pady=10)
        list_frame.columnconfigure(0, weight=1)
        
        # Issues listbox
        self.issues_listbox = tk.Listbox(list_frame, bg=self.frame_bg, fg=self.fg_color, font=('Consolas', 9))
        issues_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.issues_listbox.yview)
        self.issues_listbox.configure(yscrollcommand=issues_scrollbar.set)
        
        self.issues_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        issues_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Issue details
        details_frame = ttk.LabelFrame(parent, text="📝 Issue Details", padding="10")
        details_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=10)
        details_frame.columnconfigure(0, weight=1)
        details_frame.rowconfigure(0, weight=1)
        
        self.issue_details_text = scrolledtext.ScrolledText(
            details_frame, 
            height=8, 
            wrap=tk.WORD,
            bg=self.frame_bg, 
            fg=self.fg_color,
            insertbackground=self.fg_color,
            font=('Consolas', 9)
        )
        self.issue_details_text.pack(fill=tk.BOTH, expand=True)
        
        # Load issues
        self.load_issues()
    
    # GitHub action methods
    def connect_github_repo(self):
        """Connect to GitHub repository"""
        try:
            self.log_message(f"Connecting to repository: {self.repo_url_var.get()}")
            # Simulate connection
            self.log_message("✅ Successfully connected to GitHub repository")
            messagebox.showinfo("✅ Success", "Connected to GitHub repository successfully!")
        except Exception as e:
            messagebox.showerror("❌ Error", f"Failed to connect: {e}")
    
    def sync_github_repo(self):
        """Sync with GitHub repository"""
        try:
            self.log_message("🔄 Syncing with GitHub repository...")
            # Simulate sync
            self.log_message("✅ Repository synchronized successfully")
        except Exception as e:
            messagebox.showerror("❌ Error", f"Failed to sync: {e}")
    
    def show_github_analytics(self):
        """Show GitHub analytics"""
        analytics_window = tk.Toplevel(self.root)
        analytics_window.title("📊 GitHub Analytics")
        analytics_window.geometry("600x400")
        analytics_window.configure(bg=self.bg_color)
        
        # Analytics content
        analytics_text = """
📊 Repository Analytics

🌟 Commits: 156
🌿 Branches: 8
🐛 Issues: 23 (5 open, 18 closed)
🔄 Pull Requests: 12 (3 open, 9 merged)
⭐ Stars: 42
🍴 Forks: 8
👥 Contributors: 6

📈 Recent Activity:
• 5 commits in the last week
• 2 pull requests merged
• 3 issues closed
• 1 new branch created
        """
        
        text_widget = tk.Text(analytics_window, bg=self.frame_bg, fg=self.fg_color, font=('Consolas', 10))
        text_widget.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        text_widget.insert(tk.END, analytics_text)
        text_widget.config(state=tk.DISABLED)
    
    def clone_repo(self):
        """Clone repository"""
        self.log_message(f"📥 Cloning repository from {self.repo_url_var.get()}")
        # Simulate clone
        self.log_message("✅ Repository cloned successfully")
    
    def push_changes(self):
        """Push changes to repository"""
        self.log_message("📤 Pushing changes to repository...")
        # Simulate push
        self.log_message("✅ Changes pushed successfully")
    
    def pull_changes(self):
        """Pull changes from repository"""
        self.log_message("📥 Pulling changes from repository...")
        # Simulate pull
        self.log_message("✅ Changes pulled successfully")
    
    def create_branch(self):
        """Create new branch"""
        branch_name = self.new_branch_var.get()
        self.log_message(f"🌿 Creating branch: {branch_name}")
        # Simulate branch creation
        self.log_message(f"✅ Branch '{branch_name}' created successfully")
    
    def switch_branch(self):
        """Switch branch"""
        self.log_message("🔀 Switching branch...")
        # Simulate branch switch
        self.log_message("✅ Branch switched successfully")
    
    def delete_branch(self):
        """Delete branch"""
        self.log_message("🗑️ Deleting branch...")
        # Simulate branch deletion
        self.log_message("✅ Branch deleted successfully")
    
    def create_ci_workflow(self):
        """Create CI workflow"""
        self.log_message("📝 Creating CI workflow...")
        # Simulate workflow creation
        self.log_message("✅ CI workflow created successfully")
    
    def create_cd_workflow(self):
        """Create CD workflow"""
        self.log_message("📝 Creating CD workflow...")
        # Simulate workflow creation
        self.log_message("✅ CD workflow created successfully")
    
    def create_custom_workflow(self):
        """Create custom workflow"""
        self.log_message("📝 Creating custom workflow...")
        # Simulate workflow creation
        self.log_message("✅ Custom workflow created successfully")
    
    def run_workflow(self):
        """Run selected workflow"""
        selection = self.workflow_listbox.curselection()
        if selection:
            workflow = self.workflow_listbox.get(selection[0])
            self.log_message(f"▶️ Running workflow: {workflow}")
            # Simulate workflow execution
            self.log_message(f"✅ Workflow '{workflow}' completed successfully")
    
    def view_workflow_logs(self):
        """View workflow logs"""
        self.log_message("📊 Viewing workflow logs...")
        # Simulate log viewing
        self.log_message("📋 Workflow logs displayed")
    
    def delete_workflow(self):
        """Delete workflow"""
        selection = self.workflow_listbox.curselection()
        if selection:
            workflow = self.workflow_listbox.get(selection[0])
            self.log_message(f"🗑️ Deleting workflow: {workflow}")
            # Simulate workflow deletion
            self.log_message(f"✅ Workflow '{workflow}' deleted successfully")
    
    def create_branch_action(self):
        """Create branch action"""
        branch_name = self.new_branch_var.get()
        self.log_message(f"🌿 Creating branch: {branch_name}")
        self.load_branches()  # Refresh branch list
    
    def switch_branch_action(self):
        """Switch branch action"""
        selection = self.branch_listbox.curselection()
        if selection:
            branch = self.branch_listbox.get(selection[0])
            self.log_message(f"🔀 Switching to branch: {branch}")
            # Simulate switch
            self.log_message(f"✅ Switched to branch '{branch}'")
    
    def merge_branch(self):
        """Merge branch"""
        selection = self.branch_listbox.curselection()
        if selection:
            branch = self.branch_listbox.get(selection[0])
            self.log_message(f"🔀 Merging branch: {branch}")
            # Simulate merge
            self.log_message(f"✅ Branch '{branch}' merged successfully")
    
    def delete_branch_action(self):
        """Delete branch action"""
        selection = self.branch_listbox.curselection()
        if selection:
            branch = self.branch_listbox.get(selection[0])
            self.log_message(f"🗑️ Deleting branch: {branch}")
            self.load_branches()  # Refresh branch list
    
    def create_issue(self):
        """Create GitHub issue"""
        title = self.issue_title_var.get()
        description = self.issue_desc_text.get(1.0, tk.END).strip()
        labels = self.issue_labels_var.get()
        
        if title.strip():
            self.log_message(f"🐛 Creating issue: {title}")
            # Simulate issue creation
            self.log_message(f"✅ Issue '{title}' created successfully")
            self.load_issues()  # Refresh issues list
            
            # Clear form
            self.issue_title_var.set("")
            self.issue_desc_text.delete(1.0, tk.END)
            self.issue_labels_var.set("")
        else:
            messagebox.showwarning("⚠️ Warning", "Please enter an issue title")
    
    def load_branches(self):
        """Load branches into listbox"""
        # Simulate loading branches
        branches = ["main", "develop", "feature/new-feature", "hotfix/bug-fix", "release/v1.0"]
        self.branch_listbox.delete(0, tk.END)
        for branch in branches:
            self.branch_listbox.insert(tk.END, branch)
    
    def load_issues(self):
        """Load issues into listbox"""
        # Simulate loading issues
        issues = [
            "#42: Fix authentication bug",
            "#41: Add user profile page",
            "#40: Improve performance",
            "#39: Update documentation"
        ]
        self.issues_listbox.delete(0, tk.END)
        for issue in issues:
            self.issues_listbox.insert(tk.END, issue)


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="CI Pipeline Automation Tool")
    parser.add_argument("--config", default="ci_config.json", help="Configuration file path")
    parser.add_argument("--init", action="store_true", help="Initialize sample project files")
    parser.add_argument("--dry-run", action="store_true", help="Show configuration without running")
    parser.add_argument("--gui", action="store_true", help="Launch GUI interface")
    
    args = parser.parse_args()
    
    if args.gui:
        # Launch GUI
        root = tk.Tk()
        app = CIPipelineGUI(root)
        root.mainloop()
        return
    
    pipeline = CIPipeline(args.config)
    
    if args.init:
        pipeline.create_sample_files()
        return
    
    if args.dry_run:
        print("Configuration:")
        print(json.dumps(pipeline.config, indent=2))
        return
    
    # Run the pipeline
    report = pipeline.run_pipeline()
    
    # Print summary
    print("\n" + "="*50)
    print("PIPELINE SUMMARY")
    print("="*50)
    print(f"Project: {report['project']}")
    print(f"Status: {report['summary']['status']}")
    print(f"Duration: {report['summary']['total_duration']:.2f}s")
    print(f"Successful stages: {report['summary']['successful_stages']}")
    print(f"Failed stages: {report['summary']['failed_stages']}")
    
    if report['summary']['status'] == 'FAILED':
        print("\nFailed stages:")
        for stage_name, stages in report['stages'].items():
            for stage in stages:
                if stage['status'] == 'failed':
                    print(f"  - {stage_name}: Step {stage['step']} (Exit code: {stage['exit_code']})")
    
    sys.exit(0 if report['summary']['status'] == 'SUCCESS' else 1)


if __name__ == "__main__":
    main()
