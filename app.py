from flask import Flask, request, jsonify
from flask_cors import CORS

from job_manager import (
    fetch_all_jobs,
    fetch_unassigned_jobs,
    fetch_available_engineers,
    check_availability,
    update_job_assignment,
    mark_engineer_unavailable,
    mark_engineer_available,
    get_task_id_for_job,
    complete_job,
)

from recommender import recommend_engineers_memory_cf

app = Flask(__name__)
CORS(app)

@app.route('/jobs', methods=['GET'])
def get_jobs():
    jobs_df = fetch_all_jobs()
    jobs = jobs_df.to_dict(orient='records')
    return jsonify(jobs)

@app.route('/jobs/unassigned', methods=['GET'])
def get_unassigned_jobs():
    jobs_df = fetch_unassigned_jobs()
    jobs = jobs_df.to_dict(orient='records')
    return jsonify(jobs)

@app.route('/engineers/available', methods=['GET'])
def get_available_engineers():
    eng_df = fetch_available_engineers()
    engineers = eng_df.to_dict(orient='records')
    return jsonify(engineers)

@app.route('/jobs/assign', methods=['POST'])
def assign_engineer_to_job():
    data = request.get_json()
    job_card_id = data.get('job_card_id')

    if not job_card_id:
        return jsonify({"error": "Missing job_card_id"}), 400

    task_id = get_task_id_for_job(job_card_id)
    if not task_id:
        return jsonify({"error": "Job_Card_ID not found"}), 404

    recommendations, reason = recommend_engineers_memory_cf(task_id, top_n=5)
    if isinstance(recommendations, str):
        return jsonify({"error": recommendations}), 400

    assigned_engineer = None
    for eng_id, score in recommendations:
        if check_availability(eng_id):
            assigned_engineer = eng_id
            break

    if not assigned_engineer:
        return jsonify({"error": "No available engineer found"}), 409

    update_job_assignment(job_card_id, assigned_engineer)
    mark_engineer_unavailable(assigned_engineer)

    return jsonify({
        "message": f"Engineer {assigned_engineer} assigned to job {job_card_id}",
        "recommendation_reason": reason
    }), 200

@app.route('/jobs/complete', methods=['POST'])
def complete_job_endpoint():
    data = request.get_json()
    job_card_id = data.get('job_card_id')
    outcome_score = data.get('outcome_score')

    if not job_card_id or outcome_score is None:
        return jsonify({"error": "Missing job_card_id or outcome_score"}), 400

    try:
        complete_job(job_card_id, outcome_score)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"message": f"Job {job_card_id} marked as completed with score {outcome_score}"}), 200


if __name__ == "__main__":
    app.run(debug=True)
