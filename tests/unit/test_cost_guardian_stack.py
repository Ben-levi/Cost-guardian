import aws_cdk as core
import aws_cdk.assertions as assertions

from cost_guardian.cost_guardian_stack import CostGuardianStack

# example tests. To run these tests, uncomment this file along with the example
# resource in cost_guardian/cost_guardian_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = CostGuardianStack(app, "cost-guardian")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
