from django.apps import AppConfig
from django.urls import path, include


class TomRegionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tom_regions'
    label = 'regions'

    def target_detail_buttons(self):
        """
        Integration point for adding buttons to the target detail view.
        This method should return a list of dictionaries, each containing the keys:
        - 'namespace': The namespace of the app that provides the button's view
        - 'title': The title of the button
        - 'class': The CSS class of the button
        - 'text': The text of the button
        """
        # this is boilerplate left over from the tom_demoapp repo template
        button_config = {
            'namespace': 'regions:demo-page',
            'title': f'{self.label} Target Button',
            'class': 'btn  btn-danger',
            'text': 'Regions',
        }
        return button_config

    def nav_items(self):
        """
        Integration point for adding items to the navbar.
        This method should return a list of dictionaries that include a `partial` key pointing to the html templates to
        be included in the navbar. The `position` key, if included, should be either "left" or "right" to specify which
        side of the navbar the partial should be included on. If not included, a right side nav item is assumed.
        """
        # this puts a Regions item in the left side of the navbar -- see tom_demoapp for more examples
        navbar_items = [
            {'partial': f'{self.name}/partials/navbar_demo.html'},
        ]
        return navbar_items

    def include_url_paths(self):
        """
        Integration point for adding URL patterns to the Tom Common URL configuration.
        This method should return a list of URL patterns to be included in the main URL configuration.
        """
        urlpatterns = [
            path('regions/', include('tom_regions.urls', namespace='regions'))
        ]
        return urlpatterns

    def profile_details(self):
        """
        Integration point for adding items to the user profile page.

        This method should return a list of dictionaries that include a `partial` key pointing to the path of the html
        profile partial. The `context` key should point to the dot separated string path to the templatetag that will
        return a dictionary containing new context for the accompanying partial.
        Typically, this partial will be a bootstrap card displaying some app specific user data.
        """
        profile_config = [
            {
                'partial': f'{self.name}/partials/profile_demo.html',
                'context': f'{self.name}.templatetags.demo_extras.demo_profile_data',
            }
        ]
        return profile_config
