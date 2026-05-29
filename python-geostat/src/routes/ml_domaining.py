"""
POST /api/ml-domaining — ML-Guided Geological Domaining
Algorithms: K-Means++, HDBSCAN, GMM, Spectral Clustering
JORC/NI 43-101 compliant envelope extension (0.5× drill spacing)
"""
import time
import logging
import numpy as np
from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from scipy.spatial import KDTree
from sklearn.preprocessing import RobustScaler
from sklearn.cluster import KMeans, SpectralClustering
from sklearn.mixture import GaussianMixture
from sklearn.metrics import silhouette_score, calinski_harabasz_score, davies_bouldin_score

try:
    import hdbscan
    HAS_HDBSCAN = True
except ImportError:
    HAS_HDBSCAN = False

logger = logging.getLogger(__name__)
router = APIRouter()


def compute_drill_spacing_grid(coords: np.ndarray, k_neighbors: int = 6) -> np.ndarray:
    """
    Compute local drill spacing for each sample point.
    Returns array of local spacings (median distance to K nearest neighbors).
    """
    tree = KDTree(coords)
    # Query K+1 because the first neighbor is the point itself
    distances, _ = tree.query(coords, k=min(k_neighbors + 1, len(coords)))
    # Skip self-distance (index 0), take median of K neighbors
    local_spacings = np.median(distances[:, 1:], axis=1)
    return local_spacings


def compute_block_membership(
    block_coords: np.ndarray,
    composite_coords: np.ndarray,
    composite_labels: np.ndarray,
    composite_grades: np.ndarray,
    cutoff_grade: float,
    centroids: Optional[np.ndarray],
    features_scaler,
    block_features: Optional[np.ndarray],
    algorithm: str,
    model,
    extension_factor: float = 0.5,
    k_spacing: int = 6,
) -> dict:
    """
    Assign blocks to domains with JORC/NI 43-101 compliant distance checks.
    Returns block assignments with confidence classification.
    """
    n_blocks = len(block_coords)
    n_domains = int(composite_labels.max()) + 1
    
    # Build KDTree of qualifying composites per domain
    domain_trees = {}
    for d in range(n_domains):
        mask = (composite_labels == d) & (composite_grades >= cutoff_grade)
        if mask.sum() > 0:
            domain_trees[d] = KDTree(composite_coords[mask])
    
    # Compute local drill spacing at composite locations
    local_spacings = compute_drill_spacing_grid(composite_coords, k_spacing)
    # Build a KDTree on all composites to interpolate spacing to block locations
    all_tree = KDTree(composite_coords)
    
    # For each block, find nearest composite to get local spacing
    block_nn_dist, block_nn_idx = all_tree.query(block_coords, k=1)
    block_local_spacing = local_spacings[block_nn_idx.flatten()]
    
    # Assign blocks using ML model probabilities
    block_labels = np.full(n_blocks, -1, dtype=int)
    block_probabilities = np.zeros(n_blocks, dtype=float)
    block_confidence = np.full(n_blocks, 'excluded', dtype='U20')
    block_distance_to_data = np.zeros(n_blocks, dtype=float)
    
    # Get ML predictions for blocks
    if algorithm == 'gmm' and hasattr(model, 'predict_proba'):
        if block_features is not None:
            proba = model.predict_proba(block_features)
            ml_labels = proba.argmax(axis=1)
            ml_proba = proba.max(axis=1)
        else:
            ml_labels = np.zeros(n_blocks, dtype=int)
            ml_proba = np.ones(n_blocks)
    elif centroids is not None and block_features is not None:
        # Distance-based probability for K-Means/HDBSCAN/Spectral
        dists = np.array([np.linalg.norm(block_features - c, axis=1) for c in centroids])
        inv_dists = 1.0 / (dists + 1e-10)
        proba = inv_dists / inv_dists.sum(axis=0, keepdims=True)
        ml_labels = proba.argmax(axis=0)
        ml_proba = proba.max(axis=0)
    else:
        ml_labels = np.zeros(n_blocks, dtype=int)
        ml_proba = np.ones(n_blocks)
    
    # Apply JORC 0.5×S rule
    for i in range(n_blocks):
        domain = int(ml_labels[i])
        if domain < 0 or domain not in domain_trees:
            continue
        
        # Distance to nearest qualifying composite in this domain
        d_to_data, _ = domain_trees[domain].query(block_coords[i:i+1], k=1)
        d_to_data = float(d_to_data[0])
        block_distance_to_data[i] = d_to_data
        
        S = float(block_local_spacing[i])
        max_extension = extension_factor * S
        
        if d_to_data <= max_extension:
            block_labels[i] = domain
            block_probabilities[i] = ml_proba[i]
            block_confidence[i] = 'confident'  # Within JORC strict limit
        elif d_to_data <= S:
            block_labels[i] = domain
            block_probabilities[i] = ml_proba[i] * 0.7  # Reduced confidence
            block_confidence[i] = 'marginal'  # Between 0.5×S and S
        else:
            block_confidence[i] = 'excluded'  # Beyond drill spacing
    
    return {
        'labels': block_labels.tolist(),
        'probabilities': block_probabilities.tolist(),
        'confidence': block_confidence.tolist(),
        'distance_to_data': block_distance_to_data.tolist(),
        'local_spacing': block_local_spacing.tolist(),
    }


def run_clustering(features: np.ndarray, algorithm: str, n_clusters: int,
                   min_cluster_size: int = 15) -> dict:
    """
    Run a single clustering algorithm. Returns labels, model, centroids, metrics.
    """
    t0 = time.time()
    n_samples = len(features)
    
    if algorithm == 'kmeans':
        model = KMeans(
            n_clusters=n_clusters,
            init='k-means++',
            n_init=10,
            max_iter=300,
            random_state=42
        )
        labels = model.fit_predict(features)
        centroids = model.cluster_centers_
        
    elif algorithm == 'hdbscan':
        if not HAS_HDBSCAN:
            raise ValueError("HDBSCAN not available. Install hdbscan package.")
        model = hdbscan.HDBSCAN(
            min_cluster_size=max(min_cluster_size, 5),
            min_samples=max(min_cluster_size // 3, 2),
            cluster_selection_epsilon=0.0,
            cluster_selection_method='eom',
            prediction_data=True,
        )
        labels = model.fit_predict(features)
        # Compute centroids from assignments
        unique_labels = set(labels)
        unique_labels.discard(-1)  # Remove noise label
        centroids = np.array([features[labels == l].mean(axis=0) for l in sorted(unique_labels)])
        # Remap labels to 0-indexed (HDBSCAN may have gaps)
        label_map = {old: new for new, old in enumerate(sorted(unique_labels))}
        labels = np.array([label_map.get(l, -1) for l in labels])
        
    elif algorithm == 'gmm':
        model = GaussianMixture(
            n_components=n_clusters,
            covariance_type='full',
            n_init=5,
            max_iter=200,
            random_state=42
        )
        labels = model.fit_predict(features)
        centroids = model.means_
        
    elif algorithm == 'spectral':
        # Spectral is expensive — limit to 20K samples
        if n_samples > 20000:
            # Subsample, cluster, then assign rest via nearest centroid
            idx = np.random.RandomState(42).choice(n_samples, 20000, replace=False)
            sub_features = features[idx]
            model = SpectralClustering(
                n_clusters=n_clusters,
                affinity='nearest_neighbors',
                n_neighbors=min(15, len(sub_features) - 1),
                random_state=42,
                n_jobs=-1
            )
            sub_labels = model.fit_predict(sub_features)
            centroids = np.array([sub_features[sub_labels == l].mean(axis=0) 
                                  for l in range(n_clusters)])
            # Assign rest via nearest centroid
            tree = KDTree(centroids)
            _, nn_idx = tree.query(features, k=1)
            labels = nn_idx.flatten()
        else:
            model = SpectralClustering(
                n_clusters=n_clusters,
                affinity='nearest_neighbors',
                n_neighbors=min(15, n_samples - 1),
                random_state=42,
                n_jobs=-1
            )
            labels = model.fit_predict(features)
            centroids = np.array([features[labels == l].mean(axis=0) 
                                  for l in range(n_clusters)])
    else:
        raise ValueError(f"Unknown algorithm: {algorithm}")
    
    exec_ms = (time.time() - t0) * 1000
    
    # Compute quality metrics (only if >1 cluster and no single-point clusters)
    valid_labels = labels[labels >= 0]
    valid_features = features[labels >= 0]
    n_valid_clusters = len(set(valid_labels))
    
    metrics = {
        'n_clusters': n_valid_clusters,
        'n_noise': int((labels < 0).sum()),
        'execution_ms': round(exec_ms, 1),
    }
    
    if n_valid_clusters >= 2 and len(valid_labels) >= n_valid_clusters + 1:
        try:
            metrics['silhouette'] = round(float(silhouette_score(valid_features, valid_labels)), 4)
        except Exception:
            metrics['silhouette'] = None
        try:
            metrics['calinski_harabasz'] = round(float(calinski_harabasz_score(valid_features, valid_labels)), 2)
        except Exception:
            metrics['calinski_harabasz'] = None
        try:
            metrics['davies_bouldin'] = round(float(davies_bouldin_score(valid_features, valid_labels)), 4)
        except Exception:
            metrics['davies_bouldin'] = None
    
    return {
        'labels': labels,
        'model': model,
        'centroids': centroids,
        'metrics': metrics,
    }


def auto_find_k(features: np.ndarray, k_min: int = 2, k_max: int = 12) -> dict:
    """
    Automatically find optimal number of clusters using elbow + silhouette.
    """
    k_max = min(k_max, len(features) - 1)
    results = []
    
    for k in range(k_min, k_max + 1):
        km = KMeans(n_clusters=k, init='k-means++', n_init=5, max_iter=100, random_state=42)
        labels = km.fit_predict(features)
        inertia = float(km.inertia_)
        sil = None
        if k >= 2:
            try:
                sil = float(silhouette_score(features, labels))
            except Exception:
                pass
        results.append({'k': k, 'inertia': inertia, 'silhouette': sil})
    
    # Best K = highest silhouette
    best = max([r for r in results if r['silhouette'] is not None], 
               key=lambda r: r['silhouette'], default=results[0])
    
    return {
        'recommended_k': best['k'],
        'elbow_data': results,
    }


@router.post("/api/ml-domaining")
async def ml_domaining(request: Request):
    t0 = time.time()
    body = await request.json()
    
    try:
        # --- Input parsing ---
        action = body.get('action', 'cluster')  # 'cluster', 'auto_k', 'benchmark'
        
        # Composite data (required for clustering)
        composites_raw = body.get('composites', [])
        if not composites_raw:
            return JSONResponse({'error': 'composites array required'}, status_code=400)
        
        # Block data (optional, for envelope assignment)
        blocks_raw = body.get('blocks', [])
        
        # Feature configuration — handle both list format ["grade","x","y","z"] and dict format
        feature_config_raw = body.get('features', {})
        if isinstance(feature_config_raw, list):
            # List format from TypeScript UI: ['grade', 'x', 'y', 'z', 'cu', ...]
            feature_list = [f.lower() for f in feature_config_raw]
            use_grade = 'grade' in feature_list or 'au' in feature_list
            use_coordinates = any(c in feature_list for c in ['x', 'y', 'z'])
            spatial_weight = body.get('spatial_weight', 0.5)
            extra_attributes = [f for f in feature_list if f not in ('grade', 'au', 'x', 'y', 'z')]
            grade_weight = 1.0
        else:
            feature_config = feature_config_raw if isinstance(feature_config_raw, dict) else {}
            use_grade = feature_config.get('use_grade', True)
            use_coordinates = feature_config.get('use_coordinates', True)
            spatial_weight = feature_config.get('spatial_weight', 0.5)
            extra_attributes = feature_config.get('extra_attributes', [])
            grade_weight = feature_config.get('grade_weight', 1.0)
        
        # Algorithm configuration
        algorithm = body.get('algorithm', 'kmeans')  # kmeans, hdbscan, gmm, spectral
        n_clusters = body.get('n_clusters', 5)
        min_cluster_size = body.get('min_cluster_size', 15)
        
        # JORC envelope config
        cutoff_grade = body.get('cutoff_grade', 0.0)
        extension_factor = body.get('extension_factor', 0.5)  # JORC = 0.5
        k_spacing = body.get('k_spacing', 6)
        
        # --- Parse composites ---
        coords = np.array([[c['x'], c['y'], c['z']] for c in composites_raw], dtype=np.float64)
        grades = np.array([c.get('grade', 0.0) for c in composites_raw], dtype=np.float64)
        comp_ids = [c.get('id', str(i)) for i, c in enumerate(composites_raw)]
        
        # --- Feature engineering ---
        feature_list = []
        feature_names = []
        
        if use_grade:
            feature_list.append(grades.reshape(-1, 1) * grade_weight)
            feature_names.append('grade')
        
        for attr_name in extra_attributes:
            attr_vals = np.array([c.get(attr_name, 0.0) for c in composites_raw], dtype=np.float64)
            attr_weight = feature_config.get(f'{attr_name}_weight', 1.0)
            feature_list.append(attr_vals.reshape(-1, 1) * attr_weight)
            feature_names.append(attr_name)
        
        if use_coordinates:
            feature_list.append(coords * spatial_weight)
            feature_names.extend(['x', 'y', 'z'])
        
        if not feature_list:
            return JSONResponse({'error': 'At least one feature must be enabled'}, status_code=400)
        
        raw_features = np.hstack(feature_list)
        
        # Robust scaling (resistant to outliers)
        scaler = RobustScaler()
        features = scaler.fit_transform(raw_features)
        
        # --- Action: auto_k ---
        if action == 'auto_k':
            result = auto_find_k(features, k_min=2, k_max=min(15, len(features) - 1))
            return JSONResponse({
                'success': True,
                'action': 'auto_k',
                'recommended_k': result['recommended_k'],
                'elbow_data': result['elbow_data'],
                'execution_ms': round((time.time() - t0) * 1000, 1),
            })
        
        # --- Action: benchmark (run all algorithms, compare) ---
        if action == 'benchmark':
            algos = ['kmeans', 'gmm']
            if HAS_HDBSCAN:
                algos.append('hdbscan')
            if len(features) <= 20000:
                algos.append('spectral')
            
            benchmark_results = []
            for algo in algos:
                try:
                    res = run_clustering(features, algo, n_clusters, min_cluster_size)
                    benchmark_results.append({
                        'algorithm': algo,
                        'metrics': res['metrics'],
                    })
                except Exception as e:
                    benchmark_results.append({
                        'algorithm': algo,
                        'error': str(e),
                    })
            
            # Rank by silhouette score
            scored = [r for r in benchmark_results if r.get('metrics', {}).get('silhouette') is not None]
            if scored:
                scored.sort(key=lambda r: r['metrics']['silhouette'], reverse=True)
                recommended = scored[0]['algorithm']
            else:
                recommended = 'kmeans'
            
            return JSONResponse({
                'success': True,
                'action': 'benchmark',
                'results': benchmark_results,
                'recommended_algorithm': recommended,
                'execution_ms': round((time.time() - t0) * 1000, 1),
            })
        
        # --- Action: cluster ---
        cluster_result = run_clustering(features, algorithm, n_clusters, min_cluster_size)
        labels = cluster_result['labels']
        centroids = cluster_result['centroids']
        model = cluster_result['model']
        metrics = cluster_result['metrics']
        
        # --- Per-domain statistics ---
        domain_stats = []
        unique_domains = sorted(set(labels[labels >= 0]))
        
        # Domain colors (consistent palette)
        domain_colors = [
            '#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7',
            '#DDA0DD', '#98D8C8', '#F7DC6F', '#BB8FCE', '#85C1E9',
            '#F0B27A', '#82E0AA', '#F1948A', '#AED6F1', '#D7BDE2',
            '#A3E4D7', '#FAD7A0', '#A9CCE3', '#D5DBDB', '#EDBB99'
        ]
        
        for d in unique_domains:
            d = int(d)
            mask = labels == d
            domain_grades = grades[mask]
            domain_coords = coords[mask]
            
            # Qualifying composites (above cutoff)
            qualifying = domain_grades >= cutoff_grade
            
            stat = {
                'domain_id': d,
                'name': f'Domain {d + 1}',
                'color': domain_colors[d % len(domain_colors)],
                'composite_count': int(mask.sum()),
                'qualifying_count': int(qualifying.sum()),
                'avg_grade': round(float(domain_grades.mean()), 4) if len(domain_grades) > 0 else 0,
                'std_grade': round(float(domain_grades.std()), 4) if len(domain_grades) > 1 else 0,
                'min_grade': round(float(domain_grades.min()), 4) if len(domain_grades) > 0 else 0,
                'max_grade': round(float(domain_grades.max()), 4) if len(domain_grades) > 0 else 0,
                'median_grade': round(float(np.median(domain_grades)), 4) if len(domain_grades) > 0 else 0,
                'centroid': {
                    'x': round(float(domain_coords[:, 0].mean()), 2),
                    'y': round(float(domain_coords[:, 1].mean()), 2),
                    'z': round(float(domain_coords[:, 2].mean()), 2),
                },
                'bounding_box': {
                    'minX': round(float(domain_coords[:, 0].min()), 2),
                    'maxX': round(float(domain_coords[:, 0].max()), 2),
                    'minY': round(float(domain_coords[:, 1].min()), 2),
                    'maxY': round(float(domain_coords[:, 1].max()), 2),
                    'minZ': round(float(domain_coords[:, 2].min()), 2),
                    'maxZ': round(float(domain_coords[:, 2].max()), 2),
                },
            }
            domain_stats.append(stat)
        
        # --- Drill spacing statistics ---
        local_spacings = compute_drill_spacing_grid(coords, k_spacing)
        spacing_stats = {
            'mean': round(float(local_spacings.mean()), 2),
            'median': round(float(np.median(local_spacings)), 2),
            'min': round(float(local_spacings.min()), 2),
            'max': round(float(local_spacings.max()), 2),
            'extension_factor': extension_factor,
            'max_extension_median': round(float(np.median(local_spacings) * extension_factor), 2),
        }
        
        # --- Composite assignments ---
        composite_assignments = []
        for i, comp_id in enumerate(comp_ids):
            composite_assignments.append({
                'id': comp_id,
                'domain_id': int(labels[i]),
                'grade': round(float(grades[i]), 4),
            })
        
        # --- Block assignments (if blocks provided) ---
        block_assignments = None
        block_summary = None
        if blocks_raw:
            block_coords = np.array([[b['x'], b['y'], b['z']] for b in blocks_raw], dtype=np.float64)
            block_ids = [b.get('id', str(i)) for i, b in enumerate(blocks_raw)]
            
            # Build block features for ML prediction
            block_feature_list = []
            block_grades_arr = np.array([b.get('grade', 0.0) for b in blocks_raw], dtype=np.float64)
            if use_grade:
                block_feature_list.append(block_grades_arr.reshape(-1, 1) * grade_weight)
            for attr_name in extra_attributes:
                attr_vals = np.array([b.get(attr_name, 0.0) for b in blocks_raw], dtype=np.float64)
                attr_weight = feature_config.get(f'{attr_name}_weight', 1.0)
                block_feature_list.append(attr_vals.reshape(-1, 1) * attr_weight)
            if use_coordinates:
                block_feature_list.append(block_coords * spatial_weight)
            
            if block_feature_list:
                raw_block_features = np.hstack(block_feature_list)
                block_features = scaler.transform(raw_block_features)
            else:
                block_features = None
            
            membership = compute_block_membership(
                block_coords=block_coords,
                composite_coords=coords,
                composite_labels=labels,
                composite_grades=grades,
                cutoff_grade=cutoff_grade,
                centroids=centroids,
                features_scaler=scaler,
                block_features=block_features,
                algorithm=algorithm,
                model=model,
                extension_factor=extension_factor,
                k_spacing=k_spacing,
            )
            
            block_assignments = []
            for i, block_id in enumerate(block_ids):
                block_assignments.append({
                    'id': block_id,
                    'domain_id': membership['labels'][i],
                    'probability': round(membership['probabilities'][i], 4),
                    'confidence': membership['confidence'][i],
                    'distance_to_data': round(membership['distance_to_data'][i], 2),
                    'local_spacing': round(membership['local_spacing'][i], 2),
                })
            
            # Block summary stats
            conf_arr = np.array(membership['confidence'])
            block_summary = {
                'total_blocks': len(blocks_raw),
                'confident_blocks': int((conf_arr == 'confident').sum()),
                'marginal_blocks': int((conf_arr == 'marginal').sum()),
                'excluded_blocks': int((conf_arr == 'excluded').sum()),
                'confident_pct': round(float((conf_arr == 'confident').sum()) / len(blocks_raw) * 100, 1),
                'marginal_pct': round(float((conf_arr == 'marginal').sum()) / len(blocks_raw) * 100, 1),
                'excluded_pct': round(float((conf_arr == 'excluded').sum()) / len(blocks_raw) * 100, 1),
            }
        
        total_ms = round((time.time() - t0) * 1000, 1)
        
        response = {
            'success': True,
            'action': 'cluster',
            'algorithm': algorithm,
            'n_clusters': metrics['n_clusters'],
            'metrics': metrics,
            'domain_stats': domain_stats,
            'spacing_stats': spacing_stats,
            'composite_assignments': composite_assignments,
            'feature_names': feature_names,
            'execution_ms': total_ms,
        }
        
        if block_assignments is not None:
            response['block_assignments'] = block_assignments
            response['block_summary'] = block_summary
        
        logger.info(
            f"ML-Domaining: {algorithm}, {metrics['n_clusters']} domains, "
            f"{len(composites_raw)} composites, {len(blocks_raw)} blocks, "
            f"{total_ms:.0f}ms"
        )
        
        return JSONResponse(response)
        
    except Exception as e:
        logger.error(f"ML-Domaining error: {e}", exc_info=True)
        return JSONResponse(
            {'error': str(e), 'success': False},
            status_code=500
        )
