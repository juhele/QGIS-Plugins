"""
Plugin Name: CzechRad LOG cleaner
Description: Interactive dockable QGIS plugin to detect and clean unusable GPS/speed data 
             from Safecast / CzechRad vector point layers.
"""

import os
from qgis.PyQt.QtWidgets import (
    QDockWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QCheckBox, QSpinBox, QDoubleSpinBox, QPushButton, QTextEdit, QMessageBox, QAction
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject, QgsVectorLayer, QgsFeatureRequest, QgsWkbTypes

class CzechRadLogCleanerDockWidget(QDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("CzechRad LOG cleaner", parent)
        self.iface = iface
        self.setObjectName("CzechRadLogCleanerDockWidget")
        
        self.current_layer = None
        self.current_anomaly_fids = []
        self.scan_state = {'hdop_runs_cache': None, 'speed_runs_cache': None}
        
        # Main widget container
        from qgis.PyQt.QtWidgets import QWidget
        container = QWidget()
        
        self.init_ui(container)
        self.setWidget(container)
        self.populate_layers()

    def init_ui(self, container):
        layout = QVBoxLayout(container)
        
        # Layer selection menu & Refresh button
        layer_layout = QHBoxLayout()
        layer_label = QLabel("Select CzechRad Layer:")
        self.layer_combo = QComboBox()
        
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setFixedWidth(75)
        self.btn_refresh.clicked.connect(self.populate_layers)
        
        layer_layout.addWidget(layer_label)
        layer_layout.addWidget(self.layer_combo)
        layer_layout.addWidget(self.btn_refresh)
        layout.addLayout(layer_layout)
        
        # Checkbox 1: HDOP
        self.chk_hdop = QCheckBox("Find points with incorrect GPS (high HDOP consecutive runs)")
        self.chk_hdop.setChecked(True)
        layout.addWidget(self.chk_hdop)
        
        hdop_layout = QHBoxLayout()
        hdop_layout.addSpacing(20)
        hdop_layout.addWidget(QLabel("HDOP threshold:"))
        self.spin_hdop = QSpinBox()
        self.spin_hdop.setRange(0, 10000)
        self.spin_hdop.setValue(300)
        hdop_layout.addWidget(self.spin_hdop)
        hdop_layout.addStretch()
        layout.addLayout(hdop_layout)
        
        # Checkbox 2: Speed
        self.chk_speed = QCheckBox("Find points with low speed (stops / forgot to turn off device)")
        self.chk_speed.setChecked(True)
        layout.addWidget(self.chk_speed)
        
        speed_layout = QHBoxLayout()
        speed_layout.addSpacing(20)
        speed_layout.addWidget(QLabel("Speed threshold (km/h):"))
        self.spin_speed = QDoubleSpinBox()
        self.spin_speed.setRange(0.0, 100.0)
        self.spin_speed.setSingleStep(0.5)
        self.spin_speed.setValue(2.0)
        speed_layout.addWidget(self.spin_speed)
        speed_layout.addStretch()
        layout.addLayout(speed_layout)
        
        # Status / Log text area
        layout.addWidget(QLabel("Status & Report Log:"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        
        # Control Buttons
        btn_layout = QVBoxLayout()
        
        self.btn_start = QPushButton("Start detection")
        self.btn_start.setStyleSheet("font-weight: bold; background-color: #e0f7fa; padding: 6px;")
        self.btn_start.clicked.connect(self.start_detection)
        btn_layout.addWidget(self.btn_start)
        
        action_layout = QHBoxLayout()
        self.btn_delete = QPushButton("Delete selected points")
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self.delete_selected_points)
        
        self.btn_skip = QPushButton("Skip this selection and go to next")
        self.btn_skip.setEnabled(False)
        self.btn_skip.clicked.connect(self.skip_and_next)
        
        action_layout.addWidget(self.btn_delete)
        action_layout.addWidget(self.btn_skip)
        btn_layout.addLayout(action_layout)
        
        layout.addLayout(btn_layout)

    def get_vector_layer(self, layer):
        """Safely extracts standard QgsVectorLayer from custom wrappers like SafecastLayer."""
        if layer is None:
            return None
        if isinstance(layer, QgsVectorLayer):
            return layer
        if hasattr(layer, 'layer') and isinstance(layer.layer, QgsVectorLayer):
            return layer.layer
        if hasattr(layer, 'vectorLayer') and callable(layer.vectorLayer):
            v = layer.vectorLayer()
            if isinstance(v, QgsVectorLayer):
                return v
        return None

    def populate_layers(self):
        self.layer_combo.clear()
        layers = QgsProject.instance().mapLayers().values()
        count = 0
        for layer in layers:
            v_layer = self.get_vector_layer(layer)
            if isinstance(v_layer, QgsVectorLayer) and v_layer.geometryType() == QgsWkbTypes.PointGeometry:
                self.layer_combo.addItem(layer.name(), layer)
                count += 1
        if count == 0:
            self.layer_combo.addItem("-- No point layers found --", None)

    def log(self, message):
        self.log_text.append(message)
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def start_detection(self):
        layer_data = self.layer_combo.currentData()
        if not layer_data:
            QMessageBox.warning(self, "Warning", "No Safecast/CzechRad layer selected.")
            return
            
        v_layer = self.get_vector_layer(layer_data)
        if v_layer is None or not v_layer.isValid():
            QMessageBox.warning(self, "Warning", "Selected layer is invalid or not a vector point layer.")
            return

        # Check for mandatory attributes
        field_names = [f.name() for f in v_layer.fields()]
        missing_fields = []
        if self.chk_hdop.isChecked() and 'hdop' not in field_names:
            missing_fields.append('hdop')
        if self.chk_speed.isChecked() and 'speed_kmph' not in field_names:
            missing_fields.append('speed_kmph')
            
        if missing_fields:
            QMessageBox.warning(
                self, 
                "Missing Fields", 
                f"The selected layer is missing required attribute fields: {', '.join(missing_fields)}.\n"
                "Please select a valid Safecast/CzechRad layer."
            )
            return

        self.current_layer = layer_data
        self.scan_state = {'hdop_runs_cache': None, 'speed_runs_cache': None}
        self.log("=== Starting detection process ===")
        self.find_next_anomaly()

    def get_sorted_features(self, v_layer):
        features = list(v_layer.getFeatures())
        field_names = [f.name() for f in v_layer.fields()]
        sort_field = 'date_time' if 'date_time' in field_names else ('time_local' if 'time_local' in field_names else None)
        if sort_field:
            try:
                features.sort(key=lambda f: f[sort_field] if f[sort_field] is not None else "")
            except Exception:
                pass
        return features, field_names

    def find_next_anomaly(self):
        if not self.current_layer:
            QMessageBox.warning(self, "Warning", "No Safecast/CzechRad layer selected.")
            return
            
        v_layer = self.get_vector_layer(self.current_layer)
        if v_layer is None or not v_layer.isValid():
            QMessageBox.warning(self, "Warning", "The active layer is no longer valid.")
            return
            
        hdop_enabled = self.chk_hdop.isChecked()
        hdop_threshold = self.spin_hdop.value()
        speed_enabled = self.chk_speed.isChecked()
        speed_threshold = self.spin_speed.value()

        features, field_names = self.get_sorted_features(v_layer)

        # 1. Search for Consecutive HDOP anomalies
        if hdop_enabled:
            if self.scan_state['hdop_runs_cache'] is None:
                hdop_runs = []
                start = None
                for i, feat in enumerate(features):
                    hdop_val = feat['hdop'] if 'hdop' in field_names and feat['hdop'] is not None else 0
                    is_bad = (hdop_val >= hdop_threshold)
                    if is_bad and start is None:
                        start = i
                    elif not is_bad and start is not None:
                        fids = [features[idx].id() for idx in range(start, i)]
                        if fids:
                            hdop_runs.append(fids)
                        start = None
                if start is not None:
                    fids = [features[idx].id() for idx in range(start, len(features))]
                    if fids:
                        hdop_runs.append(fids)
                        
                self.scan_state['hdop_runs_cache'] = hdop_runs
                self.scan_state['hdop_run_index'] = 0

            runs = self.scan_state['hdop_runs_cache']
            run_idx = self.scan_state['hdop_run_index']
            
            while run_idx < len(runs):
                fids = runs[run_idx]
                existing_fids = [fid for fid in fids if v_layer.getFeature(fid).isValid()]
                if existing_fids:
                    first_feat = v_layer.getFeature(existing_fids[0])
                    time_local = first_feat['time_local'] if 'time_local' in field_names else "Unknown"
                    
                    self.current_anomaly_fids = existing_fids
                    v_layer.selectByIds(self.current_anomaly_fids)
                    
                    self.scan_state['hdop_run_index'] = run_idx + 1
                    self.log(f"[GPS/HDOP] Incorrect GPS cluster found (HDOP >= {hdop_threshold}). Local Time: {time_local} ({len(existing_fids)} points)")
                    
                    self.btn_delete.setEnabled(True)
                    self.btn_skip.setEnabled(True)
                    return
                run_idx += 1
            self.scan_state['hdop_run_index'] = run_idx

        # 2. Search for Low Speed cluster anomalies
        if speed_enabled:
            if self.scan_state['speed_runs_cache'] is None:
                speed_vals = [f['speed_kmph'] if 'speed_kmph' in field_names and f['speed_kmph'] is not None else 0.0 for f in features]
                
                runs = []
                start = None
                for i, val in enumerate(speed_vals):
                    is_low = (val <= speed_threshold)
                    if is_low and start is None:
                        start = i
                    elif not is_low and start is not None:
                        length = i - start
                        if length > 30:
                            inner_fids = [features[idx].id() for idx in range(start + 1, i - 1)]
                            if inner_fids:
                                runs.append(inner_fids)
                        start = None
                if start is not None:
                    length = len(features) - start
                    if length > 30:
                        inner_fids = [features[idx].id() for idx in range(start + 1, len(features) - 1)]
                        if inner_fids:
                            runs.append(inner_fids)
                            
                self.scan_state['speed_runs_cache'] = runs
                self.scan_state['speed_run_index'] = 0

            runs = self.scan_state['speed_runs_cache']
            run_idx = self.scan_state['speed_run_index']
            
            while run_idx < len(runs):
                fids = runs[run_idx]
                existing_fids = [fid for fid in fids if v_layer.getFeature(fid).isValid()]
                if existing_fids:
                    first_feat = v_layer.getFeature(existing_fids[0])
                    time_local = first_feat['time_local'] if 'time_local' in field_names else "Unknown"
                    
                    self.current_anomaly_fids = existing_fids
                    v_layer.selectByIds(self.current_anomaly_fids)
                    
                    self.scan_state['speed_run_index'] = run_idx + 1
                    self.log(f"[SPEED] Low speed stop cluster (>30 pts) found. Local Time of first point: {time_local} ({len(existing_fids)} points selected)")
                    
                    self.btn_delete.setEnabled(True)
                    self.btn_skip.setEnabled(True)
                    return
                run_idx += 1
            
            self.scan_state['speed_run_index'] = run_idx

        # 3. Completion
        v_layer.removeSelection()
        self.current_anomaly_fids = []
        self.btn_delete.setEnabled(False)
        self.btn_skip.setEnabled(False)
        self.log("Finished, the LOG file is clean.")
        QMessageBox.information(self, "Detection Complete", "Finished, the LOG file is clean.")

    def delete_selected_points(self):
        if not self.current_layer:
            return
        
        v_layer = self.get_vector_layer(self.current_layer)
        if v_layer is None or not v_layer.isValid():
            return
            
        selected_fids = v_layer.selectedFeatureIds()
        if not selected_fids:
            QMessageBox.warning(self, "Warning", "No points are currently selected.")
            return
            
        v_layer.startEditing()
        count = len(selected_fids)
        v_layer.deleteFeatures(selected_fids)
        v_layer.commitChanges()
        v_layer.triggerRepaint()
        
        self.log(f"-> Deleted {count} points from database.")
        
        self.scan_state['hdop_runs_cache'] = None
        self.scan_state['speed_runs_cache'] = None
        
        self.find_next_anomaly()

    def skip_and_next(self):
        self.log("-> Skipped current selection.")
        self.find_next_anomaly()


class CzechRadLogCleanerPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock_widget = None
        self.action = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), 'icon.png')
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        
        self.action = QAction(icon, "CzechRad LOG cleaner", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        
        # Add to Plugins menu
        self.iface.addPluginToMenu("CzechRad Tools", self.action)
        # Add to QGIS Toolbar
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu("CzechRad Tools", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.dock_widget:
            self.iface.removeDockWidget(self.dock_widget)
            self.dock_widget = None

    def run(self):
        if self.dock_widget is None:
            self.dock_widget = CzechRadLogCleanerDockWidget(self.iface, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock_widget)
        
        self.dock_widget.show()
        self.dock_widget.raise_()

def classFactory(iface):
    return CzechRadLogCleanerPlugin(iface)